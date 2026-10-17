"""Runtime configuration for the moment server.

Everything is driven by environment variables so the same image can run as a
throwaway local instance or as the production deployment; see ``.env.example``
and the Docker section of the README.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=False):
    """Read a boolean environment variable ("1"/"true"/"yes" count as true)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    """Read an integer environment variable, falling back on bad input."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Config:
    """Settings, overridable through environment variables."""

    SECRET_KEY = os.environ.get("MOMENT_SECRET_KEY", "moment-dev-secret-change-me")
    DATABASE_URL = os.environ.get("MOMENT_DATABASE_URL", f"sqlite:///{BASE_DIR / 'moment.db'}")

    # Bootstrap administrator, created on first start when no user exists yet.
    ADMIN_USERNAME = os.environ.get("MOMENT_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("MOMENT_ADMIN_PASSWORD", "admin123")
    # The admin also needs a watch id because the moment API is keyed by it.
    ADMIN_WATCH_ID = os.environ.get("MOMENT_ADMIN_WATCH_ID", "admin")
    # Seed the default mood/state templates and topics on the first start.
    SEED_DEFAULTS = _env_bool("MOMENT_SEED_DEFAULTS", False)
    # Escape hatch for local development (set by run.py): allow the shipped
    # default secret/admin password instead of refusing to start.
    ALLOW_INSECURE_DEFAULTS = _env_bool("MOMENT_ALLOW_INSECURE_DEFAULTS", False)

    # Session cookie hardening.  Turn ``MOMENT_SECURE_COOKIES`` on when the
    # dashboard is reached over https (directly or through a proxy).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("MOMENT_SECURE_COOKIES", False)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7

    # Uploaded media lives here and is served from /media/<name>.
    MEDIA_DIR = Path(os.environ.get("MOMENT_MEDIA_DIR", BASE_DIR / "media"))
    MAX_CONTENT_LENGTH = _env_int("MOMENT_MAX_UPLOAD_BYTES", 64 * 1024 * 1024)

    # The host the watch should be told to fetch media from.  Leave empty to
    # echo whatever host the request arrived on (right for LAN use); set it
    # when a reverse proxy rewrites the Host header.
    PUBLIC_BASE_URL = os.environ.get("MOMENT_PUBLIC_BASE_URL", "").strip().rstrip("/")

    # Number of reverse proxies in front of the app, for ``ProxyFix``.  Docker
    # compose puts none in front by default, so this defaults to 0.
    PROXY_COUNT = _env_int("MOMENT_PROXY_COUNT", 0)

    # Serve media through ``send_file`` with caching headers instead of
    # re-reading it every request.
    SEND_FILE_MAX_AGE_DEFAULT = 60 * 60 * 24

    # Log every request (method, path, status, duration) to stdout.
    LOG_REQUESTS = _env_bool("MOMENT_LOG_REQUESTS", True)

    JSON_AS_ASCII = False
