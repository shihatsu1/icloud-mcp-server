# Hosted iCloud MCP server (Cloud Run, Fly.io or any container host). Build from the repository root:
#   docker build -t icloud-mcp-server .
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FASTMCP_HOME=/data

# Pinned installer; dependencies come from the committed uv.lock
RUN pip install --no-cache-dir uv==0.11.32 \
 && useradd --create-home --uid 10001 app \
 && mkdir /data && chown app:app /data

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable \
 && chown -R app:app /app

USER app
EXPOSE 8080
# No secrets are baked in: they arrive as environment variables from the host's secret store.
# The port comes from $PORT (Cloud Run sets it) and falls back to 8080.
CMD ["/app/.venv/bin/icloud-mcp-server", "--host", "0.0.0.0"]
