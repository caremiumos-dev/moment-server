"""Production entry point: ``gunicorn wsgi:application``.

Unlike ``run.py`` this does not use the Flask development server, so it is
safe to expose.  Worker count is intentionally small: the service is a
low-traffic LAN deployment and every worker keeps its own SQLite connection.
"""

import os

from app import create_app

application = create_app()


if __name__ == "__main__":
    # Convenience for ``python wsgi.py``; gunicorn is the supported path.
    application.run(
        host=os.environ.get("MOMENT_HOST", "0.0.0.0"),
        port=int(os.environ.get("MOMENT_PORT", "8000")),
    )
