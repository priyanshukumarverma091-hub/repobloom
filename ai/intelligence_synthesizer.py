import json

from ai.ai_provider import ProviderError
from ai.base_analyst import AnalystOutput
from ai.model_router import SYN, ModelRouter
from ai.prompts import format_records, synthesizer_messages
from ai.validation import AnalysisInvalid, validate_analysis


class IntelligenceSynthesizer:
    """Model 3: combines Model 1 + Model 2 outputs into one verified-evidence analysis."""
    role = SYN

    async def run(self, router: ModelRouter, task: str, project_id: int, analyses: list[AnalystOutput],
                  records, instruction: str, question: str | None = None) -> AnalystOutput:
        payload = {a.role: a.analysis for a in analyses if a.ok and a.analysis}
        cited = {ref for a in payload.values() for sec in ("verified_facts", "inferences", "recommendations")
                 for it in a[sec] for ref in it.get("evidence", [])}
        by_ref = {f"R{r.id}": r for r in records}
        ev_recs = [by_ref[x] for x in sorted(cited) if x in by_ref][:40]
        block, refs = format_records(ev_recs, per_record_chars=500, max_records=40)
        allowed = refs or {f"R{r.id}" for r in records}
        messages = synthesizer_messages(instruction, json.dumps(payload)[:14000], block, question)
        err = ""
        for _ in range(2):
            try:
                resp = await router.call(self.role, messages, task)
                return AnalystOutput(self.role, True, validate_analysis(resp.text, allowed), refs=allowed)
            except AnalysisInvalid as e:
                err = f"invalid model output: {e}"
                messages = messages + [{"role": "assistant", "content": resp.text[:1500]},
                                       {"role": "user", "content": f"Rejected ({e}). Reply with ONLY the JSON object."}]
            except ProviderError as e:
                return AnalystOutput(self.role, False, error=str(e))
        return AnalystOutput(self.role, False, error=err)
