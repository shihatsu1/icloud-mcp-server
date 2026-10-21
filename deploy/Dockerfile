# Hosted iCloud MCP server. Build from the repository root:
#   docker build -f deploy/Dockerfile -t icloud-mcp-server .
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
CMD ["/app/.venv/bin/icloud-mcp-server", "--host", "0.0.0.0", "--port", "8080"]
