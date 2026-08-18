from __future__ import annotations

import json

from ch00chka.ai.ports import LLMGateway
from ch00chka.ai.json_tools import parse_json_object
from ch00chka.ai.prompts import OBSERVER_SYSTEM_PROMPT
from ch00chka.domain import ActorResponse, ConversationContext, ReviewResult, ReviewVerdict


class LLMAgentObserver:
    def __init__(self, *, gateway: LLMGateway, model: str) -> None:
        self._gateway = gateway
        self._model = model

    async def review(
        self,
        context: ConversationContext,
        response: ActorResponse,
    ) -> ReviewResult:
        if response.truncated:
            return ReviewResult(
                verdict=ReviewVerdict.REVISE,
                violations=("output_truncated",),
                revision_instruction=(
                    "Ответ оборвался из-за лимита. Сформулируй его заново короче, "
                    "сохрани смысл и обязательно закончи мысль."
                ),
            )
        if response.incomplete:
            return ReviewResult(
                verdict=ReviewVerdict.BLOCK,
                violations=(f"incomplete_output:{response.finish_reason}",),
            )

        payload = {
            "personality": context.personality,
            "current_message": context.current_message.text,
            "draft_response": response.text,
        }
        completion = await self._gateway.complete(
            model=self._model,
            temperature=0.0,
            max_tokens=180,
            messages=[
                {"role": "system", "content": OBSERVER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        if completion.incomplete:
            raise RuntimeError(
                f"Observer response was incomplete: {completion.finish_reason}"
            )
        data = parse_json_object(completion.text)
        try:
            verdict = ReviewVerdict(str(data.get("verdict", "block")))
        except ValueError:
            verdict = ReviewVerdict.BLOCK

        raw_violations = data.get("violations", [])
        violations = (
            tuple(str(item) for item in raw_violations)
            if isinstance(raw_violations, list)
            else ("invalid_observer_response",)
        )
        revision = data.get("revision_instruction")
        return ReviewResult(
            verdict=verdict,
            violations=violations,
            revision_instruction=str(revision) if revision else None,
        )
