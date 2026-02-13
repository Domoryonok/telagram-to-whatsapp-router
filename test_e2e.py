from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
import respx

from config import WhatsAppConfig
from content_filter import Action, FilterResult
from router import MessageRouter
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
)
from whatsapp_sender import WhatsAppSender, _split_text

WA_PHONE_ID = "111222333"
WA_RECIPIENT = "1234567890"
WA_TOKEN = "test-token"
BASE_URL = f"https://graph.facebook.com/v21.0/{WA_PHONE_ID}"
TIMESTAMP = datetime(2026, 2, 13, 14, 30, 0, tzinfo=timezone.utc)

WA_CONFIG = WhatsAppConfig(
    token=WA_TOKEN,
    phone_number_id=WA_PHONE_ID,
    recipient=WA_RECIPIENT,
)


@pytest_asyncio.fixture
async def wa():
    sender = WhatsAppSender(WA_CONFIG)
    yield sender
    await sender.close()


def _make_filter(should_forward=True, filter_name="test-filter"):
    action = Action.FORWARD if should_forward else Action.SKIP
    reason = "Valuable content" if should_forward else "Ad/promotion detected"
    result = FilterResult(action=action, reason=reason, filter_name=filter_name)
    f = MagicMock()
    f.evaluate = AsyncMock(return_value=result)
    f.close = AsyncMock()
    return f


@pytest_asyncio.fixture
async def router(wa):
    return MessageRouter(wa, _make_filter(should_forward=True))


def _make_message(text=None, photo=None, document=None, channel_username="testchannel", grouped_id=None, msg_id=42):
    chat = MagicMock()
    chat.username = channel_username
    chat.title = "TestChannel"
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.date = TIMESTAMP
    msg.photo = photo
    msg.document = document
    msg.grouped_id = grouped_id
    msg.download_media = AsyncMock(return_value=b"fake-file-bytes")
    msg.get_chat = AsyncMock(return_value=chat)
    return msg


def _make_document(mime_type="application/pdf", attributes=None):
    doc = MagicMock()
    doc.mime_type = mime_type
    doc.attributes = attributes or []
    return doc


# ---------------------------------------------------------------------------
# Media type detection tests
# ---------------------------------------------------------------------------


class TestDetectMedia:
    def test_text_only(self):
        msg = _make_message(text="hello")
        assert MessageRouter._detect_media(msg) is None

    def test_photo(self):
        msg = _make_message(photo=MagicMock())
        assert MessageRouter._detect_media(msg) == ("image", "image/jpeg", "photo.jpg")

    def test_video(self):
        attrs = [DocumentAttributeVideo(duration=10, w=1920, h=1080, round_message=False)]
        doc = _make_document("video/mp4", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("video", "video/mp4", "file")

    def test_video_note(self):
        attrs = [DocumentAttributeVideo(duration=5, w=240, h=240, round_message=True)]
        doc = _make_document("video/mp4", attrs)
        msg = _make_message(document=doc)
        wa_type, mime, filename = MessageRouter._detect_media(msg)
        assert wa_type == "video"
        assert filename == "video_note.mp4"

    def test_voice(self):
        attrs = [DocumentAttributeAudio(duration=3, voice=True)]
        doc = _make_document("audio/ogg", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("audio", "audio/ogg", "voice.ogg")

    def test_audio_file(self):
        attrs = [DocumentAttributeAudio(duration=180, voice=False)]
        doc = _make_document("audio/mpeg", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("audio", "audio/mpeg", "file")

    def test_sticker_webp(self):
        attrs = [DocumentAttributeSticker(alt="", stickerset=MagicMock())]
        doc = _make_document("image/webp", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("sticker", "image/webp", "sticker.webp")

    def test_sticker_webm(self):
        attrs = [DocumentAttributeSticker(alt="", stickerset=MagicMock())]
        doc = _make_document("video/webm", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("sticker", "video/webm", "sticker.webp")

    def test_webm_video(self):
        attrs = [DocumentAttributeVideo(duration=10, w=1920, h=1080, round_message=False)]
        doc = _make_document("video/webm", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("video", "video/webm", "file")

    def test_document_with_filename(self):
        attrs = [DocumentAttributeFilename(file_name="report.pdf")]
        doc = _make_document("application/pdf", attrs)
        msg = _make_message(document=doc)
        assert MessageRouter._detect_media(msg) == ("document", "application/pdf", "report.pdf")

    def test_empty_message(self):
        msg = _make_message()
        assert MessageRouter._detect_media(msg) is None


# ---------------------------------------------------------------------------
# E2E: full pipeline from TG message to WA API calls
# ---------------------------------------------------------------------------


@respx.mock
async def test_e2e_text_message(router):
    route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.123"}]})
    )

    msg = _make_message(text="Breaking news!")
    await router.forward("TechNews", msg)

    assert route.called
    payload = _parse_json(route.calls.last.request)
    assert payload["type"] == "text"
    assert payload["to"] == WA_RECIPIENT
    assert "[TechNews]" in payload["text"]["body"]
    assert "Breaking news!" in payload["text"]["body"]
    assert "https://t.me/testchannel/42" in payload["text"]["body"]


@respx.mock
async def test_e2e_photo_message(router):
    upload_route = respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id-photo"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.456"}]})
    )

    msg = _make_message(text="Check this out", photo=MagicMock())
    await router.forward("PhotoChannel", msg)

    assert upload_route.called
    assert send_route.called
    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "image"
    assert payload["image"]["id"] == "media-id-photo"
    assert "Check this out" in payload["image"]["caption"]


@respx.mock
async def test_e2e_video_message(router):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id-video"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.789"}]})
    )

    attrs = [DocumentAttributeVideo(duration=30, w=1920, h=1080, round_message=False)]
    doc = _make_document("video/mp4", attrs)
    msg = _make_message(text="New video", document=doc)
    await router.forward("VideoChannel", msg)

    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "video"
    assert payload["video"]["id"] == "media-id-video"


@respx.mock
async def test_e2e_document_message(router):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id-doc"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.doc"}]})
    )

    attrs = [DocumentAttributeFilename(file_name="report.pdf")]
    doc = _make_document("application/pdf", attrs)
    msg = _make_message(text="Here's the report", document=doc)
    await router.forward("DocsChannel", msg)

    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "document"
    assert payload["document"]["id"] == "media-id-doc"
    assert "report" in payload["document"]["caption"].lower()


@respx.mock
async def test_e2e_voice_message(router):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id-voice"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.voice"}]})
    )

    attrs = [DocumentAttributeAudio(duration=5, voice=True)]
    doc = _make_document("audio/ogg", attrs)
    msg = _make_message(document=doc)
    await router.forward("VoiceChannel", msg)

    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "audio"
    assert "caption" not in payload["audio"]


@respx.mock
async def test_e2e_sticker_message(router):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id-sticker"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.sticker"}]})
    )

    attrs = [DocumentAttributeSticker(alt="", stickerset=MagicMock())]
    doc = _make_document("image/webp", attrs)
    msg = _make_message(document=doc)
    await router.forward("StickerChannel", msg)

    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "sticker"
    assert "caption" not in payload["sticker"]


@respx.mock
async def test_e2e_unsupported_media_skipped_with_notification(router):
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.skip"}]})
    )

    attrs = [DocumentAttributeVideo(duration=10, w=1920, h=1080, round_message=False)]
    doc = _make_document("video/webm", attrs)
    msg = _make_message(text="Cool video", document=doc)
    await router.forward("VideoChannel", msg)

    assert send_route.called
    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "text"
    assert "Unsupported media" in payload["text"]["body"]
    assert "video/webm" in payload["text"]["body"]
    assert "https://t.me/testchannel/42" in payload["text"]["body"]


@respx.mock
async def test_e2e_media_download_failure_falls_back_to_text(router):
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.fallback"}]})
    )

    msg = _make_message(text="Photo caption text", photo=MagicMock())
    msg.download_media = AsyncMock(return_value=None)
    await router.forward("FallbackChannel", msg)

    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "text"
    assert "Photo caption text" in payload["text"]["body"]


@respx.mock
async def test_e2e_empty_message_skipped(router):
    route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={})
    )

    msg = _make_message()
    await router.forward("EmptyChannel", msg)

    assert not route.called


@respx.mock
async def test_e2e_whatsapp_api_error_propagates(wa):
    respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Invalid token"}})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await wa.send_text("This will fail")


# ---------------------------------------------------------------------------
# Content filter tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_e2e_ad_message_filtered(wa):
    route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.skip"}]})
    )

    filtered_router = MessageRouter(wa, _make_filter(should_forward=False, filter_name="ads"))
    msg = _make_message(text="Buy now! Use code SAVE20 for 20% off!")
    await filtered_router.forward("SpamChannel", msg)

    assert route.called
    payload = _parse_json(route.calls.last.request)
    assert payload["type"] == "text"
    body = payload["text"]["body"]
    assert "Skipped" in body
    assert "(ads)" in body
    assert "Ad/promotion detected" in body
    assert "https://t.me/testchannel/42" in body


@respx.mock
async def test_e2e_valuable_message_forwarded(wa):
    route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.fwd"}]})
    )

    passing_router = MessageRouter(wa, _make_filter(should_forward=True))
    msg = _make_message(text="Python 3.14 released with new features")
    await passing_router.forward("NewsChannel", msg)

    assert route.called
    payload = _parse_json(route.calls.last.request)
    assert payload["type"] == "text"
    assert "Python 3.14" in payload["text"]["body"]


@respx.mock
async def test_e2e_media_only_skips_filter(wa):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.media"}]})
    )

    # Filter would reject, but no text means filter is skipped
    strict_router = MessageRouter(wa, _make_filter(should_forward=False))
    msg = _make_message(photo=MagicMock())
    await strict_router.forward("MediaChannel", msg)

    assert send_route.called
    payload = _parse_json(send_route.calls.last.request)
    assert payload["type"] == "image"


# ---------------------------------------------------------------------------
# Album buffering tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_e2e_album_filtered(wa):
    route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.skip"}]})
    )
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id"})
    )

    router = MessageRouter(wa, _make_filter(should_forward=False, filter_name="ads"))

    # text on msg2, not first — filter still catches the whole album
    msg1 = _make_message(photo=MagicMock(), grouped_id=123456, msg_id=100)
    msg2 = _make_message(text="Buy now!", photo=MagicMock(), grouped_id=123456, msg_id=101)
    msg3 = _make_message(photo=MagicMock(), grouped_id=123456, msg_id=102)

    await router.forward("SpamChannel", msg1)
    await router.forward("SpamChannel", msg2)
    await router.forward("SpamChannel", msg3)
    await router.flush_pending()

    assert route.call_count == 1
    payload = _parse_json(route.calls.last.request)
    assert "Skipped" in payload["text"]["body"]
    assert "(ads)" in payload["text"]["body"]


@respx.mock
async def test_e2e_album_forwarded_caption_on_first_only(wa):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id-album"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.album"}]})
    )

    router = MessageRouter(wa, _make_filter(should_forward=True))

    msg1 = _make_message(text="Album caption", photo=MagicMock(), grouped_id=999, msg_id=200)
    msg2 = _make_message(photo=MagicMock(), grouped_id=999, msg_id=201)
    msg3 = _make_message(photo=MagicMock(), grouped_id=999, msg_id=202)

    await router.forward("AlbumChannel", msg1)
    await router.forward("AlbumChannel", msg2)
    await router.forward("AlbumChannel", msg3)
    await router.flush_pending()

    assert send_route.call_count == 3

    first_payload = _parse_json(send_route.calls[0].request)
    assert "Album caption" in first_payload["image"]["caption"]

    second_payload = _parse_json(send_route.calls[1].request)
    assert "caption" not in second_payload["image"]

    third_payload = _parse_json(send_route.calls[2].request)
    assert "caption" not in third_payload["image"]


@respx.mock
async def test_e2e_album_no_text_skips_filter(wa):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.notext"}]})
    )

    # filter would reject, but no text in album means filter is skipped
    content_filter = _make_filter(should_forward=False)
    router = MessageRouter(wa, content_filter)

    msg1 = _make_message(photo=MagicMock(), grouped_id=555, msg_id=300)
    msg2 = _make_message(photo=MagicMock(), grouped_id=555, msg_id=301)

    await router.forward("MediaChannel", msg1)
    await router.forward("MediaChannel", msg2)
    await router.flush_pending()

    content_filter.evaluate.assert_not_called()
    assert send_route.call_count == 2


@respx.mock
async def test_e2e_album_text_on_last_message(wa):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.last"}]})
    )

    router = MessageRouter(wa, _make_filter(should_forward=True))

    msg1 = _make_message(photo=MagicMock(), grouped_id=777, msg_id=400)
    msg2 = _make_message(photo=MagicMock(), grouped_id=777, msg_id=401)
    msg3 = _make_message(text="Late caption", photo=MagicMock(), grouped_id=777, msg_id=402)

    await router.forward("LateTextChannel", msg1)
    await router.forward("LateTextChannel", msg2)
    await router.forward("LateTextChannel", msg3)
    await router.flush_pending()

    # caption on first sent media, even though text was on msg3
    first_payload = _parse_json(send_route.calls[0].request)
    assert "Late caption" in first_payload["image"]["caption"]

    second_payload = _parse_json(send_route.calls[1].request)
    assert "caption" not in second_payload["image"]


async def test_flush_pending_empty(wa):
    router = MessageRouter(wa, _make_filter(should_forward=True))
    await router.flush_pending()  # should not raise


# ---------------------------------------------------------------------------
# Text splitting tests
# ---------------------------------------------------------------------------


class TestSplitText:
    def test_short_text_no_split(self):
        assert _split_text("hello", 100) == ["hello"]

    def test_exact_limit(self):
        text = "A" * 4096
        assert _split_text(text, 4096) == [text]

    def test_splits_at_space(self):
        text = "word " * 1000  # 5000 chars
        chunks = _split_text(text, 4096)
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)

    def test_splits_at_newline(self):
        text = "line\n" * 1000  # 5000 chars
        chunks = _split_text(text, 4096)
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)

    def test_hard_cut_no_spaces(self):
        text = "A" * 5000
        chunks = _split_text(text, 4096)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4096
        assert len(chunks[1]) == 904


@respx.mock
async def test_e2e_long_text_split(wa):
    route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.long"}]})
    )

    long_text = "A" * 5000
    results = await wa.send_text(long_text)

    assert len(results) == 2
    assert route.call_count == 2
    first_payload = _parse_json(route.calls[0].request)
    second_payload = _parse_json(route.calls[1].request)
    assert len(first_payload["text"]["body"]) == 4096
    assert len(second_payload["text"]["body"]) == 904


@respx.mock
async def test_e2e_long_caption_split(wa):
    respx.post(f"{BASE_URL}/media").mock(
        return_value=httpx.Response(200, json={"id": "media-id"})
    )
    send_route = respx.post(f"{BASE_URL}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.cap"}]})
    )

    long_caption = "B" * 2000
    media_id = await wa.upload_media(b"data", "image/jpeg", "photo.jpg")
    results = await wa.send_media("image", media_id, caption=long_caption)

    assert send_route.call_count == 2
    media_payload = _parse_json(send_route.calls[0].request)
    text_payload = _parse_json(send_route.calls[1].request)
    assert media_payload["type"] == "image"
    assert len(media_payload["image"]["caption"]) <= 1024
    assert text_payload["type"] == "text"
    total = len(media_payload["image"]["caption"]) + len(text_payload["text"]["body"])
    assert total == 2000


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_config_from_env(monkeypatch, tmp_path):
    from config import AppConfig

    # Prevent load_dotenv from overriding our test env vars
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_CHANNELS", "chan1, chan2 , chan3")
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "456")
    monkeypatch.setenv("WHATSAPP_RECIPIENT", "789")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    filters_file = tmp_path / "filters.yml"
    filters_file.write_text(
        "filters:\n"
        "  - name: spam\n"
        "    prompt: Skip spam\n"
        "  - name: clickbait\n"
        "    prompt: Skip clickbait\n"
    )

    config = AppConfig.from_env(filters_path=filters_file)

    assert config.telegram.api_id == 123
    assert config.telegram.api_hash == "abc"
    assert config.telegram.channels == ["chan1", "chan2", "chan3"]
    assert config.whatsapp.token == "tok"
    assert config.whatsapp.phone_number_id == "456"
    assert config.whatsapp.recipient == "789"
    assert config.llm is not None
    assert config.llm.api_key == "test-key"
    assert config.llm.model == "gpt-4o-mini"
    assert len(config.llm.filters) == 2
    assert config.llm.filters[0].name == "spam"
    assert config.llm.filters[1].name == "clickbait"


def test_config_no_llm(monkeypatch, tmp_path):
    from config import AppConfig

    monkeypatch.setattr("config.load_dotenv", lambda: None)
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "x")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1")
    monkeypatch.setenv("TELEGRAM_CHANNELS", "ch")
    monkeypatch.setenv("WHATSAPP_TOKEN", "x")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1")
    monkeypatch.setenv("WHATSAPP_RECIPIENT", "1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    config = AppConfig.from_env(filters_path=tmp_path / "nonexistent.yml")
    assert config.llm is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(request: httpx.Request) -> dict:
    import json
    return json.loads(request.content)
