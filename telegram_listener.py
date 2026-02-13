import logging
from pathlib import Path
from typing import Callable, Awaitable

from telethon import TelegramClient, events

from config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramListener:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        session_path = str(Path(config.session_dir) / "router")
        self.client = TelegramClient(session_path, config.api_id, config.api_hash)

    def register_handler(
        self,
        callback: Callable[[str, events.NewMessage.Event], Awaitable[None]],
    ) -> None:
        @self.client.on(events.NewMessage(chats=self.config.channels))
        async def handler(event: events.NewMessage.Event) -> None:
            chat = await event.get_chat()
            channel_name = getattr(chat, "title", str(chat.id))
            try:
                await callback(channel_name, event.message)
            except Exception:
                logger.exception("Callback failed for channel %s", channel_name)

    async def start(self) -> None:
        await self.client.start(phone=self.config.phone)
        logger.info("Telegram client started. Monitoring channels...")
        logger.info(f"Channels: {', '.join(self.config.channels)}")

    async def run(self) -> None:
        await self.client.run_until_disconnected()
