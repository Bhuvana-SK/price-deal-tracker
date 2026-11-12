# One image for every service (pipeline scheduler, dashboard, one-off commands).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    TRACKER_LOG_FORMAT=json \
    TRACKER_MODELS_DIR=/opt/models \
    PREFECT_HOME=/app/data/prefect \
    PREFECT_SERVER_ANALYTICS_ENABLED=false

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app

# 1) dependencies only (cached unless the lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) the project itself, installed non-editable
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# 3) dbt project + packages, config, dashboard, eval data
COPY dbt ./dbt
RUN cd dbt && dbt deps --profiles-dir .
COPY config ./config
COPY dashboard ./dashboard
COPY eval ./eval

# 4) bake the embedding model in so containers work offline (~64 MB)
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models')"

# Run as an unprivileged user; data lives on a volume.
RUN useradd --create-home --uid 1000 tracker && mkdir -p /app/data && chown -R tracker /app
USER tracker
VOLUME ["/app/data"]

EXPOSE 8501
CMD ["tracker", "--help"]
