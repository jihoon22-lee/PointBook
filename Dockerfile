FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

RUN uv sync --frozen --no-dev \
    && groupadd --gid 1000 pointbook \
    && useradd --uid 1000 --gid pointbook --create-home pointbook \
    && chown -R pointbook:pointbook /app

USER pointbook

EXPOSE 8000

CMD ["sh", "-c", "python -m scripts.init_db && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
