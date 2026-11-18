import json
import logging
from dataclasses import dataclass, field

from ai.ai_provider import ProviderError
from ai.model_router import ModelRouter
from ai.prompts import analyst_messages, format_records
from ai.validation import AnalysisInvalid, validate_analysis

log = logging.getLogger(__name__)


@dataclass
class AnalystOutput:
    role: str
    ok: bool
    analysis: dict | None = None
    error: str = ""
    refs: set = field(default_factory=set)


class BaseAnalyst:
    role = ""
    kinds: set = set()

    def select(self, records, limit: int = 45):
        sel = [r for r in records if r.kind in self.kinds]
        return sel[:limit]

    async def run(self, router: ModelRouter, task: str, project_id: int, records, instruction: str,
                  question: str | None = None, facts_block: str = "") -> AnalystOutput:
        recs = self.select(records)
        if not recs:
            return AnalystOutput(self.role, False, error="no relevant records for this role")
        block, refs = format_records(recs)
        messages = analyst_messages(self.role, instruction, block, question, facts_block)
        err = ""
        for attempt in range(2):          # one repair attempt if the output is not valid
            try:
                resp = await router.call(self.role, messages, task, project_id)
                return AnalystOutput(self.role, True, validate_analysis(resp.text, refs), refs=refs)
            except AnalysisInvalid as e:
                err = f"invalid model output: {e}"
                messages = messages + [
                    {"role": "assistant", "content": resp.text[:1500]},
                    {"role": "user", "content": f"Your reply was rejected ({e}). Reply again with ONLY the JSON object in the required schema."}]
            except ProviderError as e:
                return AnalystOutput(self.role, False, error=str(e))
        return AnalystOutput(self.role, False, error=err)
