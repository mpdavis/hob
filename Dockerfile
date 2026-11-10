FROM python:3.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies resolve from pyproject alone, so this layer survives edits to
# the package source.
COPY pyproject.toml README.md ./
COPY hob/__init__.py ./hob/
RUN pip install --no-cache-dir .

COPY hob/ ./hob/
RUN pip install --no-cache-dir --no-deps .

# A fixed uid so the named volume holding the SQLite state is writable without
# running as root. SQLite writes its journal beside the database, so the whole
# directory has to be writable.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin hob \
    && mkdir -p /data \
    && chown -R 1000:1000 /data
USER 1000

VOLUME ["/data"]

EXPOSE 9110

ENTRYPOINT ["hob"]
CMD ["dispatch"]
