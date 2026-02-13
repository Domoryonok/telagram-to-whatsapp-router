FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY *.py filters.yml ./

ENV SESSION_DIR=/app/data
RUN mkdir -p /app/data

CMD ["uv", "run", "--no-sync", "main.py"]
