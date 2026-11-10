FROM python:3.14-slim-bookworm

# git keeps the homelab clone current; openssh-client runs host commands
# through the hob-action allowlist.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data

WORKDIR /app

# Dependencies resolve from pyproject alone, so this layer survives edits to
# the package source.
COPY pyproject.toml README.md ./
COPY hob/__init__.py hob/prompt.md ./hob/
RUN pip install --no-cache-dir .

COPY hob/ ./hob/
RUN pip install --no-cache-dir --no-deps .

# HOME is /data so the SQLite state, the homelab clone and the Agent SDK's
# session transcripts (under ~/.claude) all live on the one named volume, and
# a thread can still be resumed after a redeploy.
RUN useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin hob \
    && mkdir -p /data /run/hob \
    && chown -R 1000:1000 /data /run/hob
USER 1000

VOLUME ["/data"]

ENTRYPOINT ["hob"]
CMD ["run"]
