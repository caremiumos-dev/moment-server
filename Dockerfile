FROM python:3.12-slim

ARG APT_MIRROR=deb.debian.org
# Optional pip index (e.g. https://mirrors.aliyun.com/pypi/simple/).
ARG PIP_INDEX_URL=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# Pillow needs these at runtime for PNG/JPEG decoding.  APT_MIRROR may be a
# host (mirrors.aliyun.com) or a full base url; both Debian source formats are
# handled so this works across python slim base image versions.
RUN set -eux; \
    mirror="${APT_MIRROR}"; \
    case "$mirror" in \
        http://*|https://*) ;; \
        *) mirror="http://$mirror" ;; \
    esac; \
    for sources in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "$sources" ]; then \
            sed -i \
                -e "s|http://deb.debian.org|$mirror|g" \
                -e "s|https://deb.debian.org|$mirror|g" \
                -e "s|http://security.debian.org|$mirror|g" \
                -e "s|https://security.debian.org|$mirror|g" \
                "$sources"; \
        fi; \
    done; \
    apt-get update \
    && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install -i "$PIP_INDEX_URL" --trusted-host "$(echo "$PIP_INDEX_URL" | sed -e 's|^https\?://||' -e 's|/.*$||')" -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

# Run as an unprivileged user; /data is the only writable location.
RUN useradd --create-home --uid 10001 moment \
    && mkdir -p /data/media \
    && chown -R moment:moment /data

COPY --chown=moment:moment app ./app
COPY --chown=moment:moment seed.py wsgi.py ./
COPY --chown=moment:moment assets ./assets
COPY --chown=moment:moment docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER moment

ENV MOMENT_DATABASE_URL=sqlite:////data/moment.db \
    MOMENT_MEDIA_DIR=/data/media \
    MOMENT_HOST=0.0.0.0 \
    MOMENT_PORT=8000

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", \
     "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "wsgi:application"]
