# Telegram-to-WhatsApp Router

Monitors selected Telegram public channels and forwards messages to your WhatsApp DMs via the Cloud API, with AI-powered content filtering.

## Features

- **Multi-channel monitoring** — subscribe to any number of public Telegram channels
- **All media types** — forwards text, photos, videos, documents, voice messages, stickers, and GIFs
- **AI content filtering (optional)** — LLM-powered filters skip ads, spam, or anything you define in `filters.yml` (works with any OpenAI-compatible provider)
- **Multiple filter rules** — add as many filters as you need; they run sequentially, first SKIP wins
- **Skip notifications** — filtered messages still send a short notice so you know what was skipped
- **Source links** — every forwarded message includes a link back to the original Telegram post
- **Smart text splitting** — long messages split at word boundaries to fit WhatsApp limits

## Supported media

| Type | Formats |
|---|---|
| Images | JPEG, PNG, WebP |
| Video | MP4, 3GPP |
| Audio | AAC, MP4, MPEG, AMR, OGG, Opus |
| Documents | PDF, Word, Excel, PowerPoint, plain text |
| Stickers | WebP |

## Limitations

- **WhatsApp media support** — formats not listed above (e.g. WebM) are skipped with a notification and a link to view the original on Telegram
- **Conversation window** — WhatsApp requires you to message the bot's number first (send "hi") to open a 24-hour conversation window before it can send messages to you
- **Userbot** — uses your personal Telegram account via Telethon, not a bot API
- **Text limits** — WhatsApp caps text at 4096 characters and captions at 1024; overflow is sent as follow-up messages

## Message format

```
[TechCrunch] 2026-02-13 14:30
https://t.me/techcrunch/5678

Breaking: New feature announced...
```

Filtered messages:

```
[Skipped from TechNews] (ads) Discount code promotion
https://t.me/technews/1234
```

Unsupported media:

```
[ChannelName] Unsupported media (video/webm) — view on Telegram
https://t.me/channel/1234
```

---

## Setup

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- A Telegram account
- A Meta (Facebook) Developer account
- (Optional) An API key from any OpenAI-compatible LLM provider for content filtering

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd tg-whatsapp-router
uv sync
```

### 2. Get Telegram API credentials

1. Go to https://my.telegram.org/apps
2. Log in with your phone number
3. Create a new application (any name/platform)
4. Copy your **API ID** and **API Hash**

### 3. Set up WhatsApp Cloud API

1. Go to https://developers.facebook.com and create a developer account
2. Click **My Apps** > **Create App** > select **Business** type
3. Add the **WhatsApp** product to your app
4. In the WhatsApp section, go to **API Setup**
5. Note your **Phone Number ID** (under "From" phone number)
6. Add your personal WhatsApp number as a recipient under **To** field and verify it
7. Generate a **Permanent Token**:
   - Go to **Business Settings** > **System Users**
   - Create a system user with admin role
   - Click **Generate New Token**, select your app, and add the `whatsapp_business_messaging` permission
   - Copy the token (the default test token expires every 24 hours — use a system user token for persistent use)

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
# Telegram
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_PHONE=+1234567890
TELEGRAM_CHANNELS=durov,techcrunch,some_news

# WhatsApp
WHATSAPP_TOKEN=EAAxxxxxxx...
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_RECIPIENT=1234567890

# LLM for content filtering (optional — omit to disable filtering)
# Any OpenAI-compatible provider works (OpenAI, Gemini, Groq, etc.)
# LLM_API_KEY=your_api_key
# LLM_MODEL=gpt-4o-mini
# LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  # for Gemini
```

- `TELEGRAM_PHONE` — your personal Telegram account phone number
- `TELEGRAM_CHANNELS` — public channel usernames you want to monitor, comma-separated without `@`
- `WHATSAPP_RECIPIENT` — your WhatsApp number in international format, digits only (no `+`)
- `LLM_API_KEY` + `LLM_MODEL` — set both to enable content filtering; omit to forward everything
- `LLM_BASE_URL` — set this to use a non-OpenAI provider (e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` for Gemini, `https://api.groq.com/openai/v1` for Groq)

### 5. Run

```bash
uv run main.py
```

On the **first run**, Telethon will ask you to enter a verification code sent to your Telegram account. After that, a `router.session` file is created and future runs won't require authentication again.

### Running with Docker

```bash
docker build -t tg-whatsapp-router .
```

**First run** — authenticate interactively to create the session file:

```bash
docker run -it --env-file .env -v ./data:/app/data tg-whatsapp-router
```

Enter the Telegram verification code when prompted. The session file is saved to `./data/`.

**Subsequent runs** — the session persists, no interaction needed:

```bash
docker run -d --env-file .env -v ./data:/app/data --restart unless-stopped tg-whatsapp-router
```

> If you use a custom `filters.yml`, it's already baked into the image at build time. To override it at runtime, add `-v ./filters.yml:/app/filters.yml`.

---

## Content filtering

Content filtering is optional. To enable it, set `LLM_API_KEY` and `LLM_MODEL` in your `.env`. Any OpenAI-compatible provider works — set `LLM_BASE_URL` to use Gemini, Groq, or other providers. If these variables are not set, all messages are forwarded without filtering.

Filter rules are defined in `filters.yml`:

```yaml
filters:
  - name: ads
    prompt: |
      SKIP these: advertisements, promotions, sponsored content, paid partnerships,
      product pitches, affiliate links, discount codes, giveaways requiring follows/shares,
      pure self-promotion — anything with no informational value.

      FORWARD these: news, tutorials, analysis, opinions, discussions, open-source
      announcements, technical content — even if it mentions a product, as long as
      there is substantive information.
```

Each filter has a `name` and a `prompt` that tells the LLM when to SKIP or FORWARD. Add as many rules as you need — they run sequentially and the first SKIP wins. If all filters pass, the message is forwarded.

## Running tests

```bash
uv run pytest -v
```
