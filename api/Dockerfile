# syntax=docker/dockerfile:1

FROM python:3.13.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --system appgroup \
    && useradd --system --gid appgroup --home-dir /app appuser \
    && chown -R appuser:appgroup /app
USER appuser
EXPOSE 8000
CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000"]
