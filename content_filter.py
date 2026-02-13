import enum
import logging

from openai import AsyncOpenAI
from pydantic import BaseModel

from config import LLMConfig

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are a content filter for a Telegram-to-WhatsApp message forwarder.
Analyze the message and decide if it should be forwarded or skipped based on the following rule:

{prompt}"""


class Action(enum.Enum):
    FORWARD = "FORWARD"
    SKIP = "SKIP"


class FilterDecision(BaseModel):
    action: Action
    reason: str


class FilterResult(BaseModel):
    action: Action
    reason: str
    filter_name: str


class ContentFilter:
    # first SKIP wins across all filter rules

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)

    async def evaluate(self, text: str) -> FilterResult:
        if not text or not text.strip():
            return FilterResult(action=Action.FORWARD, reason="No text to analyze", filter_name="none")

        if not self.config.filters:
            return FilterResult(action=Action.FORWARD, reason="No filters configured", filter_name="none")

        for rule in self.config.filters:
            decision = await self._run_filter(rule.name, rule.prompt, text)
            logger.info(f"Filter [{rule.name}]: {decision.action.value} — {decision.reason}")
            if decision.action == Action.SKIP:
                return decision

        return FilterResult(action=Action.FORWARD, reason="Passed all filters", filter_name="all")

    async def _run_filter(self, name: str, prompt: str, text: str) -> FilterResult:
        system_prompt = BASE_SYSTEM_PROMPT.format(prompt=prompt)
        try:
            response = await self.client.beta.chat.completions.parse(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                response_format=FilterDecision,
            )
            decision = response.choices[0].message.parsed
            return FilterResult(
                action=decision.action,
                reason=decision.reason,
                filter_name=name,
            )
        except Exception:
            logger.exception(f"Filter [{name}] failed, forwarding by default")
            return FilterResult(action=Action.FORWARD, reason="Filter error", filter_name=name)

    async def close(self) -> None:
        await self.client.close()
