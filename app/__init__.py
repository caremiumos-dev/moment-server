"""Flask application factory for the self-hosted moment service."""

import os

from flask import Flask, send_from_directory

from app.config import Config


def create_app(config_object=Config, overrides=None):
    """Build the Flask app and make sure the database and admin exist."""
    app = Flask(__name__)
    app.config.from_object(config_object)
    if overrides:
        app.config.update(overrides)

    media_dir = str(app.config["MEDIA_DIR"])
    os.makedirs(media_dir, exist_ok=True)

    from app.models import init_engine

    init_engine(app.config["DATABASE_URL"])

    from app.api import api
    from app.api_extra import extra
    from app.auth import auth
    from app.dashboard import dashboard

    app.register_blueprint(api)
    app.register_blueprint(extra)
    app.register_blueprint(auth)
    app.register_blueprint(dashboard)

    @app.get("/media/<path:name>")
    def media(name):
        """Serve files placed in ``MOMENT_MEDIA_DIR``.

        Nothing writes here yet because picture uploads still go straight to
        Qiniu from the watch (see README); the route exists so the download
        endpoints can return real urls once files are dropped in manually.
        """
        return send_from_directory(media_dir, name)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    ensure_admin(app)
    return app


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
