# Multi-stage build using uv for fast, reproducible installs.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies only, keyed on the lockfiles: this stage never sees the source, so a
# code change cannot invalidate the multi-GB venv. The project itself is NOT installed
# into the venv; the runtime stage puts the source on PYTHONPATH instead.
# Avoid BuildKit-only RUN --mount syntax so this image builds on Minikube/Colima
# Docker daemons that do not ship with buildx.
COPY pyproject.toml uv.lock /app/
# `connectors` = the ingestion connector SDKs the Sources UI offers. google-api-python-
# client bundles ~110 MB of discovery documents for every Google API; the gdrive
# connector only builds Drive v3 (static discovery), so the rest are dropped.
RUN uv sync --frozen --no-install-project --no-dev --extra browser --extra ocr --extra sandbox --extra connectors \
    && find /app/.venv/lib/python3.12/site-packages/googleapiclient/discovery_cache/documents \
        -type f ! -name 'drive.v3.json' -delete


FROM python:3.12-slim-bookworm AS runtime

# Non-root runtime user.
RUN groupadd --system app && useradd --system --gid app --create-home app

# Layer order runs from least to most frequently changing: system packages, the
# venv (changes only with pyproject.toml/uv.lock), the Playwright browser, then the
# application source (a few MB), so a code-only change rebuilds just the last layer.
# Install Playwright system dependencies for Chromium (must run as root).
# libxmlsec1(-openssl): XML-DSig runtime for SAML SSO (python3-saml/xmlsec). The
# xmlsec manylinux wheel bundles its own copy; the system library keeps the image
# working if a platform without a wheel builds xmlsec from source.
# apt over HTTPS: on some networks plain-HTTP responses from deb.debian.org arrive
# altered ("invalid signature" on InRelease, "Hash Sum mismatch" on .debs).
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    tesseract-ocr \
    tesseract-ocr-hin \
    poppler-utils \
    libxml2 libxmlsec1 libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/*

# The project is not pip-installed, so provide its `agentverse` console script here.
RUN printf '#!/bin/sh\nexec python -m app.cli.main "$@"\n' > /usr/local/bin/agentverse \
    && chmod 0755 /usr/local/bin/agentverse

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# PYTHONPATH makes `app` importable for uvicorn, alembic and celery without installing
# the project into the venv (which would tie the venv layer to the source).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

# Install Playwright Chromium browser as the app user so the runtime process
# can locate it at /home/app/.cache/ms-playwright. This is optional for small
# Minikube smoke profiles; production/RPA builds should keep it enabled. Outside
# development /health and /health/ready report "rpa_browser" down without it
# (app/rpa/readiness.py): an image built with INSTALL_PLAYWRIGHT=false must run
# with RPA_BROWSER_REQUIRED=false.
# Runs before the source COPY so code changes do not re-download the browser.
ARG INSTALL_PLAYWRIGHT=true
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then playwright install chromium; else echo "Skipping Playwright browser install"; fi

# Application source last (small layer; the only one a code change rebuilds).
COPY --chown=app:app alembic.ini /app/
COPY --chown=app:app scripts /app/scripts
COPY --chown=app:app app /app/app

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/livez', timeout=2).status==200 else 1)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
