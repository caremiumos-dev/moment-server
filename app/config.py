"""Runtime configuration for the moment server."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """Settings, overridable through environment variables."""

    SECRET_KEY = os.environ.get("MOMENT_SECRET_KEY", "moment-dev-secret-change-me")
    DATABASE_URL = os.environ.get("MOMENT_DATABASE_URL", f"sqlite:///{BASE_DIR / 'moment.db'}")

    # Bootstrap administrator, created on first start when no user exists yet.
    ADMIN_USERNAME = os.environ.get("MOMENT_ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("MOMENT_ADMIN_PASSWORD", "admin123")
    # The admin also needs a watch id because the moment API is keyed by it.
    ADMIN_WATCH_ID = os.environ.get("MOMENT_ADMIN_WATCH_ID", "admin")

    # Session cookie hardening.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7

    # Uploaded media lives here and is served from /media/<name>.
    MEDIA_DIR = Path(os.environ.get("MOMENT_MEDIA_DIR", BASE_DIR / "media"))
    MAX_CONTENT_LENGTH = 64 * 1024 * 1024

    JSON_AS_ASCII = False
