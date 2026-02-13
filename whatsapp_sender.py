import logging

import httpx

from config import WhatsAppConfig

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com/v21.0"

MAX_TEXT = 4096
MAX_CAPTION = 1024


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n ")
    return chunks


class WhatsAppSender:
    def __init__(self, config: WhatsAppConfig) -> None:
        self.phone_number_id = config.phone_number_id
        self.recipient = config.recipient
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers={"Authorization": f"Bearer {config.token}"},
        )

    @property
    def _messages_url(self) -> str:
        return f"{BASE_URL}/{self.phone_number_id}/messages"

    @property
    def _media_url(self) -> str:
        return f"{BASE_URL}/{self.phone_number_id}/media"

    async def send_text(self, text: str) -> list[dict]:
        # splits into multiple messages if over MAX_TEXT
        results = []
        for chunk in _split_text(text, MAX_TEXT):
            payload = {
                "messaging_product": "whatsapp",
                "to": self.recipient,
                "type": "text",
                "text": {"body": chunk},
            }
            response = await self.client.post(self._messages_url, json=payload)
            if response.is_error:
                logger.error(f"WhatsApp API error: {response.text}")
            response.raise_for_status()
            results.append(response.json())
        return results

    async def upload_media(self, file_bytes: bytes, mime_type: str, filename: str) -> str:
        data = {"messaging_product": "whatsapp", "type": mime_type}
        files = {"file": (filename, file_bytes, mime_type)}
        response = await self.client.post(self._media_url, data=data, files=files)
        if response.is_error:
            logger.error(f"WhatsApp media upload error: {response.text}")
        response.raise_for_status()
        return response.json()["id"]

    async def send_media(self, media_type: str, media_id: str, caption: str | None = None) -> list[dict]:
        # caption overflow (>1024) goes as a follow-up text message
        media_object: dict = {"id": media_id}
        overflow: str | None = None
        if caption and media_type in ("image", "video", "document"):
            if len(caption) > MAX_CAPTION:
                cut = caption.rfind(" ", 0, MAX_CAPTION)
                if cut <= 0:
                    cut = MAX_CAPTION
                media_object["caption"] = caption[:cut]
                overflow = caption[cut:].lstrip()
            else:
                media_object["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "to": self.recipient,
            "type": media_type,
            media_type: media_object,
        }
        response = await self.client.post(self._messages_url, json=payload)
        response.raise_for_status()
        results = [response.json()]
        if overflow:
            results.extend(await self.send_text(overflow))
        return results

    async def close(self) -> None:
        await self.client.aclose()
