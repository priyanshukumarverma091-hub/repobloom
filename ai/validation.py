"""Validation of model output. Anti-fabrication rule: a 'verified fact' must cite at least one
evidence ref that really was supplied; otherwise it is demoted to a low-confidence inference."""
import json
import re

MAX_ITEMS = 25
MAX_LEN = 600


class AnalysisInvalid(ValueError):
    pass


def _extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise AnalysisInvalid("response is not JSON")
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise AnalysisInvalid(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise AnalysisInvalid("JSON root must be an object")
    return obj


def _refs(val, allowed: set[str]) -> list[str]:
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list):
        return []
    out = []
    for v in val:
        v = str(v).strip().strip("[]")
        if v in allowed and v not in out:
            out.append(v)
    return out


def _stmt(item) -> str | None:
    if isinstance(item, str):
        item = {"statement": item}
    if not isinstance(item, dict):
        return None
    s = str(item.get("statement") or item.get("text") or "").strip()
    return s[:MAX_LEN] or None


def _conf(v, default=0.5) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def validate_analysis(text: str, allowed_refs: set[str]) -> dict:
    obj = _extract_json(text)
    facts, infs, recs, demoted = [], [], [], 0
    for it in (obj.get("verified_facts") or [])[:MAX_ITEMS]:
        s = _stmt(it)
        if not s:
            continue
        ev = _refs(it.get("evidence") if isinstance(it, dict) else [], allowed_refs)
        if ev:
            facts.append({"statement": s, "evidence": ev})
        else:
            demoted += 1
            infs.append({"statement": s, "evidence": [], "confidence": 0.25,
                         "note": "demoted from fact: no valid evidence cited"})
    for it in (obj.get("inferences") or [])[:MAX_ITEMS]:
        s = _stmt(it)
        if s:
            d = it if isinstance(it, dict) else {}
            infs.append({"statement": s, "evidence": _refs(d.get("evidence"), allowed_refs),
                         "confidence": _conf(d.get("confidence"))})
    for it in (obj.get("recommendations") or [])[:MAX_ITEMS]:
        s = _stmt(it)
        if s:
            d = it if isinstance(it, dict) else {}
            pr = str(d.get("priority", "medium")).lower()
            recs.append({"statement": s, "evidence": _refs(d.get("evidence"), allowed_refs),
                         "priority": pr if pr in ("high", "medium", "low") else "medium"})
    unknown = [str(u).strip()[:MAX_LEN] for u in (obj.get("unknown") or [])[:MAX_ITEMS] if str(u).strip()]
    summary = str(obj.get("summary") or "").strip()[:1500]
    if not (facts or infs or recs or unknown or summary):
        raise AnalysisInvalid("analysis is empty")
    return {"summary": summary, "verified_facts": facts, "inferences": infs,
            "recommendations": recs, "unknown": unknown, "demoted": demoted}
