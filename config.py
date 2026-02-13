import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_FILTERS_PATH = Path(__file__).parent / "filters.yml"


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    phone: str
    channels: list[str]
    session_dir: str = "."


@dataclass(frozen=True)
class WhatsAppConfig:
    token: str
    phone_number_id: str
    recipient: str


@dataclass(frozen=True)
class FilterRule:
    name: str
    prompt: str


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: str | None = None
    filters: list[FilterRule] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    whatsapp: WhatsAppConfig
    llm: LLMConfig | None = None

    @classmethod
    def from_env(cls, filters_path: Path | None = None) -> "AppConfig":
        load_dotenv()
        channels_raw = os.environ["TELEGRAM_CHANNELS"]
        channels = [ch.strip() for ch in channels_raw.split(",") if ch.strip()]

        path = filters_path or DEFAULT_FILTERS_PATH
        filters = _load_filters(path)

        llm_api_key = os.environ.get("LLM_API_KEY")
        llm_model = os.environ.get("LLM_MODEL")
        llm_config = None
        if llm_api_key and llm_model:
            llm_config = LLMConfig(
                api_key=llm_api_key,
                model=llm_model,
                base_url=os.environ.get("LLM_BASE_URL") or None,
                filters=filters,
            )

        return cls(
            telegram=TelegramConfig(
                api_id=int(os.environ["TELEGRAM_API_ID"]),
                api_hash=os.environ["TELEGRAM_API_HASH"],
                phone=os.environ["TELEGRAM_PHONE"],
                channels=channels,
                session_dir=os.environ.get("SESSION_DIR", "."),
            ),
            whatsapp=WhatsAppConfig(
                token=os.environ["WHATSAPP_TOKEN"],
                phone_number_id=os.environ["WHATSAPP_PHONE_NUMBER_ID"],
                recipient=os.environ["WHATSAPP_RECIPIENT"],
            ),
            llm=llm_config,
        )


def _load_filters(path: Path) -> list[FilterRule]:
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data or "filters" not in data:
        return []
    return [
        FilterRule(name=entry["name"], prompt=entry["prompt"].strip())
        for entry in data["filters"]
    ]
