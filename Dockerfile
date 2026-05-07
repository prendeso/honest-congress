# syntax=docker/dockerfile:1.7
#
# Honest Congress production image. Two-stage build keeps the runtime image
# lean (no compilers, no pip cache) while still providing the system libs
# pdfplumber + lxml need.
#
# Build:  docker build -t honest-congress .
# Run:    docker run -p 8000:8000 -e DATABASE_URL=... honest-congress

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for lxml/pdfplumber etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    ENV=production

# Runtime libs only — no compilers in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN useradd --create-home --uid 10001 honest
WORKDIR /app

COPY --from=builder /install /usr/local
COPY . /app

RUN chown -R honest:honest /app
USER honest

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:${PORT}/health || exit 1

# Railway / local: respects $PORT. uvicorn is invoked directly so signals
# (SIGTERM) propagate cleanly for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn src.api.main:app --host ${HOST} --port ${PORT}"]
