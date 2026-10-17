"""Flask application factory for the self-hosted moment service."""

import logging
import os
import time

from flask import Flask, g, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config


def create_app(config_object=Config, overrides=None):
    """Build the Flask app and make sure the database and admin exist."""
    app = Flask(__name__)
    app.config.from_object(config_object)
    if overrides:
        app.config.update(overrides)

    _configure_logging(app)
    proxy_count = int(app.config.get("PROXY_COUNT") or 0)
    if proxy_count > 0:
        # Only trust the headers when a known number of proxies sits in front;
        # otherwise a client could spoof its own host and scheme.
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=proxy_count, x_proto=proxy_count, x_host=proxy_count
        )

    media_dir = str(app.config["MEDIA_DIR"])
    os.makedirs(media_dir, exist_ok=True)

    from app.models import init_engine

    init_engine(app.config["DATABASE_URL"])

    from app.api import api
    from app.api_extra import extra
    from app.auth import auth
    from app.dashboard import dashboard

    _check_secret(app)

    app.register_blueprint(api)
    app.register_blueprint(extra)
    app.register_blueprint(auth)
    app.register_blueprint(dashboard)

    @app.get("/media/<path:name>")
    def media(name):
        """Serve uploaded pictures, videos and template icons."""
        return send_from_directory(media_dir, name)

    @app.get("/healthz")
    def healthz():
        """Liveness probe: the process is up and can answer HTTP."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        """Readiness probe: the database is reachable and the schema exists."""
        from sqlalchemy import text as sql_text

        from app.models import get_session

        session = get_session()
        try:
            session.execute(sql_text("SELECT 1"))
            return {"status": "ok"}
        except Exception:  # pragma: no cover - exercised only when the db is down
            app.logger.exception("readiness check failed")
            return {"status": "unavailable"}, 503
        finally:
            session.close()

    if app.config.get("LOG_REQUESTS", True):
        @app.before_request
        def _start_timer():
            g.request_started = time.monotonic()

        @app.after_request
        def _log_request(response):
            started = g.get("request_started")
            duration = (time.monotonic() - started) * 1000 if started else 0.0
            app.logger.info(
                "%s %s -> %s %.1fms %s",
                request.method,
                request.path,
                response.status_code,
                duration,
                request.headers.get("Content-Length") or "-",
            )
            return response

    ensure_admin(app)
    return app


def _check_secret(app):
    """Refuse to start production with the shipped development secret."""
    secret = str(app.config.get("SECRET_KEY") or "")
    if app.config.get("TESTING") or app.config.get("DEBUG"):
        return
    if secret and secret != "moment-dev-secret-change-me":
        # A real secret is configured; nothing to complain about.
        return
    if app.config.get("ALLOW_INSECURE_DEFAULTS"):
        app.logger.warning(
            "MOMENT_SECRET_KEY is the development default and insecure defaults "
            "are enabled; do not expose this instance to the internet"
        )
        return
    raise RuntimeError(
        "MOMENT_SECRET_KEY is still the development default; "
        "set it to a long random value before exposing the service"
    )


def _configure_logging(app):
    """Send application and request logs to stdout for ``docker logs``."""
    level = os.environ.get("MOMENT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app.logger.setLevel(getattr(logging, level, logging.INFO))


def ensure_admin(app):
    """Create the bootstrap administrator the first time the app starts."""
    from app.models import User, get_session

    session = get_session()
    try:
        if session.query(User).count() == 0:
            admin = User(
                username=app.config["ADMIN_USERNAME"],
                watch_id=app.config["ADMIN_WATCH_ID"],
                display_name="管理员",
                is_admin=True,
                is_active=True,
            )
            admin.set_password(app.config["ADMIN_PASSWORD"])
            session.add(admin)
            session.commit()
            app.logger.info("created bootstrap administrator %s", admin.username)
    finally:
        session.close()
