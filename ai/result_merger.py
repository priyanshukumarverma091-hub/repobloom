"""Merge validated analyses from several models, de-duplicating statements and keeping provenance."""
import re


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()[:160]


def _merge_list(items_by_role: list[tuple[str, list[dict]]], keep_conf: bool = False) -> list[dict]:
    seen: dict[str, dict] = {}
    for role, items in items_by_role:
        for it in items:
            k = _norm(it["statement"])
            if k in seen:
                cur = seen[k]
                cur["evidence"] = sorted(set(cur.get("evidence", [])) | set(it.get("evidence", [])))
                if role not in cur["by"]:
                    cur["by"].append(role)
                if keep_conf:
                    cur["confidence"] = max(cur.get("confidence", 0), it.get("confidence", 0))
            else:
                seen[k] = dict(it, by=[role])
    return list(seen.values())


def merge_analyses(parts: list[tuple[str, dict]]) -> dict:
    parts = [(r, a) for r, a in parts if a]
    if not parts:
        return {}
    facts = _merge_list([(r, a["verified_facts"]) for r, a in parts])
    fact_keys = {_norm(f["statement"]) for f in facts}
    infs = [i for i in _merge_list([(r, a["inferences"]) for r, a in parts], keep_conf=True)
            if _norm(i["statement"]) not in fact_keys]
    recs = _merge_list([(r, a["recommendations"]) for r, a in parts])
    unknown, seen = [], set()
    for _, a in parts:
        for u in a["unknown"]:
            if _norm(u) not in seen:
                seen.add(_norm(u))
                unknown.append(u)
    return {"summary": " ".join(a["summary"] for _, a in parts if a.get("summary"))[:1500],
            "verified_facts": facts, "inferences": infs, "recommendations": recs, "unknown": unknown,
            "demoted": sum(a.get("demoted", 0) for _, a in parts)}
