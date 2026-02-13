import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)

from content_filter import Action, ContentFilter
from whatsapp_sender import WhatsAppSender

logger = logging.getLogger(__name__)

WA_SUPPORTED_MIMES = frozenset({
    "audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg", "audio/opus",
    "application/vnd.ms-powerpoint", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/pdf", "text/plain", "application/vnd.ms-excel",
    "image/jpeg", "image/png", "image/webp",
    "video/mp4", "video/3gpp",
})


@dataclass
class _PendingAlbum:
    channel_name: str
    messages: list = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None


class MessageRouter:
    def __init__(
        self,
        sender: WhatsAppSender,
        content_filter: ContentFilter | None = None,
        album_timeout: float = 0.5,
    ) -> None:
        self.sender = sender
        self.filter = content_filter
        self._album_timeout = album_timeout
        self._pending_albums: dict[int, _PendingAlbum] = {}

    async def forward(self, channel_name: str, message) -> None:
        grouped_id = getattr(message, "grouped_id", None)

        if not grouped_id:
            await self._forward_single(channel_name, message)
            return

        # buffer album messages, flush after a sliding window
        if grouped_id in self._pending_albums:
            album = self._pending_albums[grouped_id]
            album.messages.append(message)
            if album.timer is not None:
                album.timer.cancel()
        else:
            album = _PendingAlbum(channel_name=channel_name, messages=[message])
            self._pending_albums[grouped_id] = album

        loop = asyncio.get_running_loop()
        album.timer = loop.call_later(
            self._album_timeout,
            lambda gid=grouped_id: asyncio.ensure_future(self._flush_album(gid)),
        )

    async def _forward_single(self, channel_name: str, message) -> None:
        timestamp = message.date
        chat = await message.get_chat()
        source_link = self._build_source_link(chat, message.id)

        if message.text and self.filter:
            result = await self.filter.evaluate(message.text)
            logger.info(
                f"[{channel_name}] filter={result.filter_name} "
                f"action={result.action.value} reason={result.reason}"
            )
            if result.action == Action.SKIP:
                skip_msg = f"[Skipped from {channel_name}] ({result.filter_name}) {result.reason}"
                if source_link:
                    skip_msg += f"\n{source_link}"
                await self.sender.send_text(skip_msg)
                return

        media_info = self._detect_media(message)

        if media_info is None:
            if not message.text:
                return
            text = self._format_text(channel_name, timestamp, message.text, source_link)
            await self.sender.send_text(text)
            logger.info(f"Forwarded text from [{channel_name}]")
            return

        wa_type, mime_type, filename = media_info

        if mime_type not in WA_SUPPORTED_MIMES:
            skip_msg = f"[{channel_name}] Unsupported media ({mime_type}) — view on Telegram"
            if source_link:
                skip_msg += f"\n{source_link}"
            await self.sender.send_text(skip_msg)
            logger.info(f"Skipped unsupported {mime_type} from [{channel_name}]")
            return

        caption = self._format_caption(channel_name, timestamp, message.text, source_link)

        file_bytes = await message.download_media(bytes)
        if file_bytes is None:
            logger.warning(f"Could not download media from [{channel_name}], msg {message.id}")
            if message.text:
                await self.sender.send_text(self._format_text(channel_name, timestamp, message.text, source_link))
            return

        media_id = await self.sender.upload_media(file_bytes, mime_type, filename)
        await self.sender.send_media(wa_type, media_id, caption=caption)
        logger.info(f"Forwarded {wa_type} from [{channel_name}]")

    async def _flush_album(self, grouped_id: int) -> None:
        album = self._pending_albums.pop(grouped_id, None)
        if album is None:
            return

        channel_name = album.channel_name
        messages = album.messages

        # find text from whichever message has the caption
        album_text = next((m.text for m in messages if m.text), None)

        first_msg = messages[0]
        chat = await first_msg.get_chat()
        source_link = self._build_source_link(chat, first_msg.id)
        timestamp = first_msg.date

        if album_text and self.filter:
            result = await self.filter.evaluate(album_text)
            logger.info(
                f"[{channel_name}] album filter={result.filter_name} "
                f"action={result.action.value} reason={result.reason}"
            )
            if result.action == Action.SKIP:
                skip_msg = f"[Skipped from {channel_name}] ({result.filter_name}) {result.reason}"
                if source_link:
                    skip_msg += f"\n{source_link}"
                await self.sender.send_text(skip_msg)
                logger.info(f"Skipped album ({len(messages)} items) from [{channel_name}]")
                return

        caption = self._format_caption(channel_name, timestamp, album_text, source_link)
        is_first = True

        for msg in messages:
            media_info = self._detect_media(msg)
            if media_info is None:
                continue

            wa_type, mime_type, filename = media_info

            if mime_type not in WA_SUPPORTED_MIMES:
                logger.info(f"Skipped unsupported {mime_type} in album from [{channel_name}]")
                continue

            file_bytes = await msg.download_media(bytes)
            if file_bytes is None:
                logger.warning(f"Could not download album media from [{channel_name}], msg {msg.id}")
                continue

            media_id = await self.sender.upload_media(file_bytes, mime_type, filename)
            await self.sender.send_media(wa_type, media_id, caption=caption if is_first else None)
            logger.info(f"Forwarded album {wa_type} from [{channel_name}]")
            is_first = False

        # all downloads failed — fall back to text
        if is_first and album_text:
            text = self._format_text(channel_name, timestamp, album_text, source_link)
            await self.sender.send_text(text)
            logger.info(f"Forwarded album text-only from [{channel_name}]")

    async def flush_pending(self) -> None:
        for gid in list(self._pending_albums):
            album = self._pending_albums.get(gid)
            if album and album.timer is not None:
                album.timer.cancel()
            await self._flush_album(gid)

    @staticmethod
    def _detect_media(message) -> tuple[str, str, str] | None:
        # -> (wa_type, mime, filename) or None
        if message.photo:
            return ("image", "image/jpeg", "photo.jpg")

        if not message.document:
            return None

        doc = message.document
        mime = doc.mime_type or "application/octet-stream"
        filename = "file"
        is_voice = is_video = is_audio = is_sticker = is_gif = False

        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
            elif isinstance(attr, DocumentAttributeVideo):
                is_video = True
                if attr.round_message:
                    filename = "video_note.mp4"
            elif isinstance(attr, DocumentAttributeAudio):
                is_audio = True
                if attr.voice:
                    is_voice = True
                    filename = "voice.ogg"
            elif isinstance(attr, DocumentAttributeSticker):
                is_sticker = True
                filename = "sticker.webp"
            elif isinstance(attr, DocumentAttributeAnimated):
                is_gif = True

        if is_sticker:
            return ("sticker", mime, filename)
        if is_voice:
            return ("audio", "audio/ogg", filename)
        if is_audio:
            return ("audio", mime, filename)
        if is_video or is_gif:
            return ("video", mime, filename)
        return ("document", mime, filename)

    @staticmethod
    def _build_source_link(chat, message_id: int) -> str | None:
        username = getattr(chat, "username", None)
        if username:
            return f"https://t.me/{username}/{message_id}"
        return None

    @staticmethod
    def _format_header(channel_name: str, timestamp: datetime, source_link: str | None = None) -> str:
        local_time = timestamp.astimezone().strftime("%Y-%m-%d %H:%M")
        header = f"[{channel_name}] {local_time}"
        if source_link:
            header += f"\n{source_link}"
        return header

    @classmethod
    def _format_text(cls, channel_name: str, timestamp: datetime, text: str, source_link: str | None = None) -> str:
        return f"{cls._format_header(channel_name, timestamp, source_link)}\n\n{text}"

    @classmethod
    def _format_caption(cls, channel_name: str, timestamp: datetime, text: str | None, source_link: str | None = None) -> str:
        header = cls._format_header(channel_name, timestamp, source_link)
        if text:
            return f"{header}\n\n{text}"
        return header
