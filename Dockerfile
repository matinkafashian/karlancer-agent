FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN sed -i \
        -e 's|http://archive.ubuntu.com/ubuntu/|http://de.archive.ubuntu.com/ubuntu/|g' \
        -e 's|http://security.ubuntu.com/ubuntu/|http://de.archive.ubuntu.com/ubuntu/|g' \
        /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
        curl \
        nodejs \
        novnc \
        npm \
        postgresql-client \
        python3.12-venv \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN npm install --global @openai/codex@0.149.1 \
    && npm cache clean --force

COPY --from=builder /wheels /wheels
RUN python -m venv "$VIRTUAL_ENV" \
    && python -m pip install --no-cache-dir --no-index --find-links=/wheels karlancer-agent \
    && rm -rf /wheels

WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/container-login.sh /usr/local/bin/karlancer-login
COPY scripts/codex-login.sh /usr/local/bin/codex-login
RUN chmod 0755 /usr/local/bin/karlancer-login /usr/local/bin/codex-login \
    && mkdir -p /data/browser-profile /data/codex /data/evidence \
    && chown -R pwuser:pwuser /data /app

USER pwuser

CMD ["uvicorn", "karlancer_agent.production:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
