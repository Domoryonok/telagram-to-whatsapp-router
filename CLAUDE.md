# CLAUDE.md

Guidance for AI assistants working on this codebase.

## Project Overview

Telegram-to-WhatsApp message router. Monitors public Telegram channels via Telethon (userbot) and forwards all message types (text, photos, videos, documents, voice, stickers) to WhatsApp DMs via Meta's Cloud API.

## Commands

- **Run**: `uv run main.py`
- **Test**: `uv run pytest -v`
- **Add dependency**: `uv add <package>`
- **Sync dependencies**: `uv sync`

## Tech Stack

- Python 3.13 (managed via `.python-version`)
- `uv` for project/dependency management
- Telethon — Telegram userbot client
- httpx — async HTTP client for WhatsApp Cloud API
- python-dotenv — env config loading
- pytest + pytest-asyncio + respx — testing

## Architecture

- `main.py` — entry point, media type detection, message formatting, forwarding logic
- `telegram_listener.py` — Telethon client setup and channel event handler
- `whatsapp_sender.py` — WhatsApp Cloud API client (text, media upload, media send)
- `test_e2e.py` — end-to-end tests with mocked APIs
- `.env` — configuration (not committed)
