from __future__ import annotations

import argparse
import asyncio
import json

from ch00chka.ai.actor import LLMAgentActor
from ch00chka.ai.gateway import DeepSeekGateway
from ch00chka.ai.observer import LLMAgentObserver
from ch00chka.ai.participation import LLMAgentParticipationDecider
from ch00chka.ai.prompts import PROMPT_VERSION
from ch00chka.domain import ConversationContext, NormalizedMessage
from config import Settings


async def run(text: str, force_reply: bool) -> None:
    settings = Settings(bot_token="unused-for-agent-test")
    gateway = DeepSeekGateway(
        api_key=settings.AI_api_token.get_secret_value(),
        base_url=settings.ai_base_url,
        thinking_enabled=settings.ai_thinking_enabled,
    )
    message = NormalizedMessage(
        chat_id=1,
        message_id=1,
        user_id=1,
        user_name="Тестировщик",
        text=text,
        chat_type="group",
        bot_name="Ch00chKA",
        mentions_bot=force_reply,
    )
    class EmptyRepository:
        async def recent_messages(self, *, chat_id, limit):
            return tuple()

    participation = LLMAgentParticipationDecider(
        gateway=gateway,
        model=settings.ai_participation_model,
        repository=EmptyRepository(),
    )
    decision = await participation.decide(message)
    print("Participation:")
    print(json.dumps({
        "should_reply": decision.should_reply,
        "reason": decision.reason,
        "confidence": decision.confidence,
    }, ensure_ascii=False, indent=2))
    if not decision.should_reply and not force_reply:
        return

    context = ConversationContext(
        personality=settings.default_personality,
        summary="",
        recent_messages=(),
        current_message=message,
        prompt_version=PROMPT_VERSION,
    )
    actor = LLMAgentActor(gateway=gateway, model=settings.ai_model)
    observer = LLMAgentObserver(gateway=gateway, model=settings.ai_observer_model)
    response = await actor.respond(context)
    review = await observer.review(context, response)
    print("\nActor:")
    print(response.text)
    print("\nObserver:")
    print(json.dumps({
        "verdict": review.verdict,
        "violations": review.violations,
        "revision_instruction": review.revision_instruction,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Test AI agents without Telegram")
    parser.add_argument("message", help="Test chat message")
    parser.add_argument(
        "--force-reply",
        action="store_true",
        help="Treat the message as an explicit bot mention",
    )
    args = parser.parse_args()
    asyncio.run(run(args.message, args.force_reply))


if __name__ == "__main__":
    main()
