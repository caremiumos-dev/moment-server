"""Development entry point.

    python run.py

Listens on 0.0.0.0:8000 so a watch on the same LAN can reach it.
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("MOMENT_HOST", "0.0.0.0"),
        port=int(os.environ.get("MOMENT_PORT", "8000")),
        debug=os.environ.get("MOMENT_DEBUG", "0") == "1",
    )
