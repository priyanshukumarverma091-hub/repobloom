"""Rule-based classifier (fast, deterministic, no API cost)."""
import re

CATEGORIES = ["bug", "feature", "decision", "refactor", "docs", "task", "question", "discussion", "other"]

_RULES = {
    "bug": [r"\bbug\b", r"\bfix(es|ed)?\b", r"\berror\b", r"exception", r"traceback", r"\bcrash", r"\bbroken\b",
            r"regression", r"\bfail(s|ed|ing|ure)?\b", r"\b500\b", r"doesn'?t work", r"not working", r"\bhotfix\b"],
    "feature": [r"\bfeat(ure)?\b", r"\badd(s|ed)?\b", r"\bimplement", r"\bsupport for\b", r"\bnew\b", r"\bintroduce"],
    "decision": [r"we (decided|agreed)", r"let'?s (go with|use|stick)", r"decision", r"\binstead of\b",
                 r"\bADR\b", r"we('ll| will) (use|go with|adopt)", r"trade-?off", r"chose|chosen", r"going with"],
    "refactor": [r"refactor", r"clean ?up", r"\brename", r"\bmigrate\b", r"restructure", r"\bcleanup\b"],
    "docs": [r"\bdocs?\b", r"readme", r"documentation", r"\bguide\b", r"\btutorial\b", r"changelog"],
    "task": [r"\btodo\b", r"\bfixme\b", r"follow[- ]?up", r"\bneed(s)? to\b", r"\bremaining\b", r"\bnext step"],
    "question": [r"\?\s*$", r"\bhow (do|can|to)\b", r"\bwhy (does|is|do)\b", r"anyone (know|tried)"],
}
_COMPILED = {k: [re.compile(p, re.I | re.M) for p in v] for k, v in _RULES.items()}
_KIND_PRIOR = {"doc": "docs", "branch": "other", "contributor": "other", "file_tree": "other"}


def classify(kind: str, title: str, content: str, labels: list[str] | None = None) -> tuple[str, float]:
    if kind in _KIND_PRIOR:
        return _KIND_PRIOR[kind], 0.9
    text = f"{title}\n{content[:2000]}"
    scores = {c: sum(1 for p in pats if p.search(text)) for c, pats in _COMPILED.items()}
    for lab in labels or []:
        l = lab.lower()
        if "bug" in l or "defect" in l:
            scores["bug"] += 3
        if "feature" in l or "enhancement" in l:
            scores["feature"] += 3
        if "doc" in l:
            scores["docs"] += 3
        if "question" in l or "help" in l:
            scores["question"] += 2
    # a decision cue beats generic words like "use"/"add"
    if scores["decision"] >= 1 and scores["decision"] >= max(scores["bug"], scores["feature"]) - 1:
        scores["decision"] += 1
    best = max(scores, key=lambda c: scores[c])
    if scores[best] == 0:
        return ("discussion" if kind == "message" else "other"), 0.2
    total = sum(scores.values())
    return best, round(min(0.95, 0.4 + 0.55 * scores[best] / total), 2)
