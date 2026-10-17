#!/bin/sh
# Container entrypoint: seed the defaults once, then hand off to the real
# command (gunicorn).  Doing this here instead of in the app factory keeps the
# seeding to a single process; every gunicorn worker imports the app and would
# otherwise race to insert the same rows.
set -e

if [ "${MOMENT_SEED_DEFAULTS:-false}" = "true" ]; then
    python seed.py
fi

exec "$@"
