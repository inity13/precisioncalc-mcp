# PrecisionCalc MCP -- production container image
# Build:  docker build -t precisioncalc-mcp .
# Run (HTTP):   docker run --rm -p 8000:8000 -e PRECISIONCALC_API_KEYS=your-key precisioncalc-mcp
# Run (stdio):  docker run --rm -i precisioncalc-mcp python server.py stdio

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PRECISIONCALC_HOST=0.0.0.0 \
    PRECISIONCALC_PORT=8000 \
    PRECISIONCALC_FX_PROVIDER=frankfurter

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App source.
COPY . .

# Drop root.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Lightweight healthcheck hits the metered /metrics endpoint (open in no-auth mode).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=4); sys.exit(0)" || exit 1

# Default to streamable HTTP so the container is reachable over a port.
CMD ["python", "server.py", "http"]
