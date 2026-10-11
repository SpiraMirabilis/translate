# T9 container image. Build and run through compose.yaml (`docker compose up -d`);
# see QUICKSTART-Docker.md.

# ---- Frontend build ---------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/frontend/ ./
# The Library imports tag_vocab.json from the repo root at build time.
COPY tag_vocab.json /src/tag_vocab.json
RUN npm run build

# ---- Codex CLI (for the `codex` provider) -------------------------------------
# The npm package is a Node wrapper around a static native binary; only that
# binary is kept. Not --platform=$BUILDPLATFORM: npm must fetch the binary for
# the image's own architecture.
FROM node:22-slim AS codex
ARG CODEX_VERSION=0.160.1
RUN npm install -g --no-audit --no-fund "@openai/codex@${CODEX_VERSION}" \
 && bin=$(find /usr/local/lib/node_modules/@openai -path '*/vendor/*/bin/codex' -type f | head -n1) \
 && test -n "$bin" && install -m 755 "$bin" /codex

# ---- Runtime ----------------------------------------------------------------
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODEX_HOME=/data/codex \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt web/requirements_web.txt ./
RUN pip install -r requirements.txt -r requirements_web.txt && rm requirements_web.txt

COPY . .
COPY --from=frontend /src/web/frontend/dist web/frontend/dist

# All runtime state lives in /data. The app writes these paths relative to the
# repo root, so each one is a symlink into the volume (created dangling here;
# the entrypoint creates the targets).
RUN useradd --uid 1000 --create-home t9 \
 && for f in database.db settings.json .env ip_cache.db translate.log \
             covers illustrations epub_cache output logs sitemaps; do \
        rm -rf "/app/$f"; ln -s "/data/$f" "/app/$f"; \
    done \
 && for f in cedict.db cedict_ts.u8; do \
        rm -rf "/app/web/$f"; ln -s "/data/$f" "/app/web/$f"; \
    done

COPY --from=codex /codex /usr/local/lib/codex/codex
COPY docker/codex-wrapper.sh /usr/local/bin/codex
COPY docker/entrypoint.sh /usr/local/bin/t9-entrypoint
RUN chmod 755 /usr/local/bin/t9-entrypoint /usr/local/bin/codex

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8).status == 200 else 1)"

ENTRYPOINT ["t9-entrypoint"]
CMD ["admin"]
