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
- openai (AsyncOpenAI) — LLM-based content filtering (any OpenAI-compatible provider)
- pydantic — structured LLM responses
- pyyaml — filter rules config
- python-dotenv — env config loading
- cryptg — faster Telegram media downloads
- pytest + pytest-asyncio + respx — testing

## Architecture

- `main.py` — thin entry point; wires config, listener, router, sender, and filter together
- `config.py` — dataclass-based config (`AppConfig`, `TelegramConfig`, `WhatsAppConfig`, `LLMConfig`, `FilterRule`); loads from env vars and `filters.yml`
- `telegram_listener.py` — Telethon client setup, channel event handler, calls registered callback on new messages
- `router.py` — `MessageRouter`: core forwarding logic, media type detection, message formatting, album grouping (sliding-window flush), content filter integration, source link generation
- `whatsapp_sender.py` — `WhatsAppSender`: WhatsApp Cloud API client (text with auto-split at 4096 chars, media upload, media send with caption overflow handling)
- `content_filter.py` — `ContentFilter`: runs messages through LLM-based filter rules (first SKIP wins); uses OpenAI structured outputs (`response_format`) for FORWARD/SKIP decisions
- `filters.yml` — YAML-defined filter rules (name + prompt pairs); currently has an ads filter
- `test_e2e.py` — end-to-end tests with mocked Telegram and WhatsApp APIs
- `test_router.py` — unit tests for `MessageRouter` (single messages, albums, filtering, edge cases)
- `.env` / `.env.example` — configuration (not committed / template)
- `Dockerfile` — production container (python:3.13-slim + uv, session stored in `/app/data`)
