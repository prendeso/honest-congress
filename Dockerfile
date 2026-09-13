# syntax=docker/dockerfile:1.7
#
# Honest Congress production image. Two-stage build keeps the runtime image
# lean (no compilers, no pip cache) while still providing the system libs
# pdfplumber + lxml need.
#
# Build:  docker build -t honest-congress .
# Run:    docker run -p 8000:8000 -e DATABASE_URL=... honest-congress

# ---------- builder ----------
# 3.11 to match CI and `target-version` in pyproject.toml. The image used to
# build on 3.12, so the tests had never run on the version that shipped.
FROM python:3.11-slim AS builder

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
FROM python:3.11-slim AS runtime

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

# Trust the X-Forwarded-* headers from Railway's TLS-terminating proxy.
#
# uvicorn already enables --proxy-headers by default (Config.proxy_headers is
# True), so adding that flag would change nothing. The lever that was actually
# missing is this one: forwarded_allow_ips defaults to "127.0.0.1", and Railway's
# proxy reaches the container from a non-loopback address, so every
# X-Forwarded-Proto: https was discarded and the app believed it was serving
# plain HTTP.
#
# What that broke: FastAPI answers a URL missing its trailing slash with a 307 to
# an ABSOLUTE url built from the scheme it thinks it has -- so
# GET /api/anomalies?member_id=12217 redirected to
# http://<host>/api/anomalies/?member_id=12217. The browser blocks that as mixed
# content on an HTTPS page, fetch() rejects, and the members page reported
# "Failed to load anomalies for this member." curl followed it happily, which is
# why the endpoint looked healthy from a terminal. A 307 also re-sends method,
# body and headers, so the same downgrade applied to an admin route would have
# put ADMIN_PASSWORD or X-Admin-Token on the wire in cleartext.
#
# Set as an environment variable rather than --forwarded-allow-ips='*' on the
# command line: this CMD runs through `sh -c`, where an unquoted * is glob-expanded
# against WORKDIR /app and uvicorn dies at startup on the resulting file list.
# uvicorn reads this variable directly (Config: os.environ.get("FORWARDED_ALLOW_IPS",
# "127.0.0.1")), so there is no shell in the path at all. It also covers every
# entrypoint -- the CMD below, start_server.py and `cli serve` -- from one place.
#
# "*" trusts the forwarded headers from any peer. That is safe here because the
# container is only reachable through Railway's proxy; the residual effect is that
# a spoofed X-Forwarded-For would be recorded as the client address in access logs,
# and no application code reads the client address for anything.
ENV FORWARDED_ALLOW_IPS="*"

# Railway / local: respects $PORT. uvicorn is invoked directly so signals
# (SIGTERM) propagate cleanly for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn src.api.main:app --host ${HOST} --port ${PORT}"]
