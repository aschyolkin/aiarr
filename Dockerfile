FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY mae ./mae
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" MAE_STATE_DIR=/state PYTHONDONTWRITEBYTECODE=1
USER 3000:3000
ENTRYPOINT ["mae"]
CMD ["api"]
