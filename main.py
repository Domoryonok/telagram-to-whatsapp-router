import asyncio
import logging

from config import AppConfig
from content_filter import ContentFilter
from router import MessageRouter
from telegram_listener import TelegramListener
from whatsapp_sender import WhatsAppSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    config = AppConfig.from_env()

    sender = WhatsAppSender(config.whatsapp)
    content_filter = ContentFilter(config.llm) if config.llm else None
    listener = TelegramListener(config.telegram)
    router = MessageRouter(sender, content_filter)

    listener.register_handler(router.forward)

    await listener.start()
    try:
        await listener.run()
    finally:
        await sender.close()
        if content_filter:
            await content_filter.close()


if __name__ == "__main__":
    asyncio.run(main())
