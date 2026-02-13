from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from content_filter import Action, FilterResult
from router import MessageRouter

TIMESTAMP = datetime(2026, 2, 13, 14, 30, 0, tzinfo=timezone.utc)


def _mock_sender():
    s = MagicMock()
    s.send_text = AsyncMock()
    s.upload_media = AsyncMock(return_value="media-id-123")
    s.send_media = AsyncMock()
    s.close = AsyncMock()
    return s


def _mock_filter(action=Action.FORWARD, reason="ok", name="test"):
    result = FilterResult(action=action, reason=reason, filter_name=name)
    f = MagicMock()
    f.evaluate = AsyncMock(return_value=result)
    return f


def _msg(text=None, photo=None, document=None, username="chan", grouped_id=None, msg_id=1):
    chat = MagicMock()
    chat.username = username
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.date = TIMESTAMP
    msg.photo = photo
    msg.document = document
    msg.grouped_id = grouped_id
    msg.download_media = AsyncMock(return_value=b"bytes")
    msg.get_chat = AsyncMock(return_value=chat)
    return msg


# ---------------------------------------------------------------------------
# Static/class method tests
# ---------------------------------------------------------------------------


class TestBuildSourceLink:
    def test_with_username(self):
        chat = MagicMock()
        chat.username = "mychannel"
        assert MessageRouter._build_source_link(chat, 99) == "https://t.me/mychannel/99"

    def test_without_username(self):
        chat = MagicMock(spec=[])  # no username attr
        assert MessageRouter._build_source_link(chat, 99) is None

    def test_username_is_none(self):
        chat = MagicMock()
        chat.username = None
        assert MessageRouter._build_source_link(chat, 99) is None


class TestFormatHeader:
    def test_basic(self):
        header = MessageRouter._format_header("News", TIMESTAMP)
        assert "[News]" in header
        assert "2026" in header

    def test_with_source_link(self):
        header = MessageRouter._format_header("News", TIMESTAMP, "https://t.me/ch/1")
        assert "https://t.me/ch/1" in header

    def test_without_source_link(self):
        header = MessageRouter._format_header("News", TIMESTAMP, None)
        assert "t.me" not in header


class TestFormatText:
    def test_includes_header_and_body(self):
        result = MessageRouter._format_text("Ch", TIMESTAMP, "hello world")
        assert "[Ch]" in result
        assert "hello world" in result

    def test_includes_source_link(self):
        result = MessageRouter._format_text("Ch", TIMESTAMP, "body", "https://t.me/ch/1")
        assert "https://t.me/ch/1" in result


class TestFormatCaption:
    def test_with_text(self):
        result = MessageRouter._format_caption("Ch", TIMESTAMP, "caption text")
        assert "[Ch]" in result
        assert "caption text" in result

    def test_without_text(self):
        result = MessageRouter._format_caption("Ch", TIMESTAMP, None)
        assert "[Ch]" in result
        # header only, no double newline for body
        assert result.count("\n\n") == 0


# ---------------------------------------------------------------------------
# _forward_single tests
# ---------------------------------------------------------------------------


class TestForwardSingle:
    async def test_text_message(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        await router.forward("News", _msg(text="hello"))

        sender.send_text.assert_called_once()
        assert "hello" in sender.send_text.call_args[0][0]

    async def test_empty_message_skipped(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        await router.forward("Ch", _msg())

        sender.send_text.assert_not_called()
        sender.upload_media.assert_not_called()

    async def test_photo_uploads_and_sends(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        await router.forward("Ch", _msg(text="cap", photo=MagicMock()))

        sender.upload_media.assert_called_once_with(b"bytes", "image/jpeg", "photo.jpg")
        sender.send_media.assert_called_once()
        args = sender.send_media.call_args
        assert args[0][0] == "image"
        assert args[0][1] == "media-id-123"
        assert "cap" in args[1]["caption"]

    async def test_filter_skip_sends_notification(self):
        sender = _mock_sender()
        f = _mock_filter(Action.SKIP, "spam detected", "spam")
        router = MessageRouter(sender, f)

        await router.forward("Ch", _msg(text="buy now"))

        f.evaluate.assert_called_once_with("buy now")
        sender.send_text.assert_called_once()
        text = sender.send_text.call_args[0][0]
        assert "Skipped" in text
        assert "(spam)" in text
        sender.upload_media.assert_not_called()

    async def test_filter_forward_sends_normally(self):
        sender = _mock_sender()
        f = _mock_filter(Action.FORWARD, "looks good", "quality")
        router = MessageRouter(sender, f)

        await router.forward("Ch", _msg(text="good content"))

        f.evaluate.assert_called_once()
        sender.send_text.assert_called_once()
        assert "good content" in sender.send_text.call_args[0][0]

    async def test_no_filter_configured(self):
        sender = _mock_sender()
        router = MessageRouter(sender, content_filter=None)

        await router.forward("Ch", _msg(text="anything"))

        sender.send_text.assert_called_once()

    async def test_media_without_text_skips_filter(self):
        sender = _mock_sender()
        f = _mock_filter(Action.SKIP, "would reject", "strict")
        router = MessageRouter(sender, f)

        await router.forward("Ch", _msg(photo=MagicMock()))

        f.evaluate.assert_not_called()
        sender.upload_media.assert_called_once()
        sender.send_media.assert_called_once()

    async def test_download_failure_falls_back_to_text(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        msg = _msg(text="caption", photo=MagicMock())
        msg.download_media = AsyncMock(return_value=None)

        await router.forward("Ch", msg)

        sender.upload_media.assert_not_called()
        sender.send_text.assert_called_once()
        assert "caption" in sender.send_text.call_args[0][0]

    async def test_download_failure_no_text_sends_nothing(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        msg = _msg(photo=MagicMock())
        msg.download_media = AsyncMock(return_value=None)

        await router.forward("Ch", msg)

        sender.send_text.assert_not_called()
        sender.send_media.assert_not_called()

    async def test_source_link_in_text(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        await router.forward("Ch", _msg(text="hi", username="mychan", msg_id=55))

        text = sender.send_text.call_args[0][0]
        assert "https://t.me/mychan/55" in text

    async def test_source_link_in_skip_notification(self):
        sender = _mock_sender()
        f = _mock_filter(Action.SKIP, "bad", "f1")
        router = MessageRouter(sender, f)

        await router.forward("Ch", _msg(text="x", username="mychan", msg_id=10))

        text = sender.send_text.call_args[0][0]
        assert "https://t.me/mychan/10" in text

    async def test_no_source_link_when_no_username(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        msg = _msg(text="hello")
        chat = MagicMock(spec=[])  # no username
        msg.get_chat = AsyncMock(return_value=chat)

        await router.forward("Ch", msg)

        text = sender.send_text.call_args[0][0]
        assert "t.me" not in text


# ---------------------------------------------------------------------------
# Album buffering tests
# ---------------------------------------------------------------------------


class TestAlbumBuffer:
    async def test_grouped_messages_are_buffered(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=100, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=100, msg_id=2))

        # nothing sent yet — still buffered
        sender.upload_media.assert_not_called()
        assert 100 in router._pending_albums
        assert len(router._pending_albums[100].messages) == 2

    async def test_non_grouped_processed_immediately(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        await router.forward("Ch", _msg(text="hi"))

        sender.send_text.assert_called_once()
        assert len(router._pending_albums) == 0

    async def test_flush_sends_all_media(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=2))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=3))
        await router.flush_pending()

        assert sender.upload_media.call_count == 3
        assert sender.send_media.call_count == 3
        assert len(router._pending_albums) == 0

    async def test_album_caption_on_first_only(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        await router.forward("Ch", _msg(text="cap", photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=2))
        await router.flush_pending()

        calls = sender.send_media.call_args_list
        assert "cap" in calls[0][1]["caption"]
        assert calls[1][1]["caption"] is None

    async def test_album_text_found_on_any_message(self):
        sender = _mock_sender()
        f = _mock_filter(Action.FORWARD, "ok", "f")
        router = MessageRouter(sender, f)

        # text on third message
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=2))
        await router.forward("Ch", _msg(text="found me", photo=MagicMock(), grouped_id=10, msg_id=3))
        await router.flush_pending()

        f.evaluate.assert_called_once_with("found me")
        first_caption = sender.send_media.call_args_list[0][1]["caption"]
        assert "found me" in first_caption

    async def test_album_filter_skip_discards_all(self):
        sender = _mock_sender()
        f = _mock_filter(Action.SKIP, "ad", "ads")
        router = MessageRouter(sender, f)

        await router.forward("Ch", _msg(text="buy", photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=2))
        await router.flush_pending()

        # only the skip notification
        sender.send_text.assert_called_once()
        assert "Skipped" in sender.send_text.call_args[0][0]
        sender.upload_media.assert_not_called()
        sender.send_media.assert_not_called()

    async def test_album_no_text_skips_filter(self):
        sender = _mock_sender()
        f = _mock_filter(Action.SKIP, "would reject", "strict")
        router = MessageRouter(sender, f)

        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=2))
        await router.flush_pending()

        f.evaluate.assert_not_called()
        assert sender.send_media.call_count == 2

    async def test_album_no_filter_configured(self):
        sender = _mock_sender()
        router = MessageRouter(sender, content_filter=None)

        await router.forward("Ch", _msg(text="cap", photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=2))
        await router.flush_pending()

        assert sender.send_media.call_count == 2

    async def test_album_download_failure_skips_that_item(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        good = _msg(photo=MagicMock(), grouped_id=10, msg_id=1)
        bad = _msg(photo=MagicMock(), grouped_id=10, msg_id=2)
        bad.download_media = AsyncMock(return_value=None)

        await router.forward("Ch", good)
        await router.forward("Ch", bad)
        await router.flush_pending()

        assert sender.upload_media.call_count == 1
        assert sender.send_media.call_count == 1

    async def test_album_all_downloads_fail_falls_back_to_text(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        msg1 = _msg(text="caption", photo=MagicMock(), grouped_id=10, msg_id=1)
        msg1.download_media = AsyncMock(return_value=None)
        msg2 = _msg(photo=MagicMock(), grouped_id=10, msg_id=2)
        msg2.download_media = AsyncMock(return_value=None)

        await router.forward("Ch", msg1)
        await router.forward("Ch", msg2)
        await router.flush_pending()

        sender.send_media.assert_not_called()
        sender.send_text.assert_called_once()
        assert "caption" in sender.send_text.call_args[0][0]

    async def test_album_all_downloads_fail_no_text_sends_nothing(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        msg1 = _msg(photo=MagicMock(), grouped_id=10, msg_id=1)
        msg1.download_media = AsyncMock(return_value=None)

        await router.forward("Ch", msg1)
        await router.flush_pending()

        sender.send_text.assert_not_called()
        sender.send_media.assert_not_called()

    async def test_concurrent_albums_independent(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        await router.forward("A", _msg(text="a", photo=MagicMock(), grouped_id=1, msg_id=1))
        await router.forward("B", _msg(text="b", photo=MagicMock(), grouped_id=2, msg_id=2))
        await router.forward("A", _msg(photo=MagicMock(), grouped_id=1, msg_id=3))

        assert len(router._pending_albums) == 2
        await router.flush_pending()

        assert sender.upload_media.call_count == 3
        assert sender.send_media.call_count == 3
        assert len(router._pending_albums) == 0

    async def test_flush_pending_idempotent(self):
        sender = _mock_sender()
        router = MessageRouter(sender)

        await router.forward("Ch", _msg(photo=MagicMock(), grouped_id=10, msg_id=1))
        await router.flush_pending()
        await router.flush_pending()  # second call is a no-op

        assert sender.upload_media.call_count == 1

    async def test_flush_album_unknown_id_is_noop(self):
        sender = _mock_sender()
        router = MessageRouter(sender)
        await router._flush_album(99999)  # not in pending

        sender.send_text.assert_not_called()
        sender.send_media.assert_not_called()
