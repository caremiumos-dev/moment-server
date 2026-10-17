"""Absolute URLs for files stored under ``MEDIA_DIR``.

The dashboard stores uploaded files as ``media:<name>`` because it has no
request host to build a URL from; the expansion to a full URL happens while a
watch request is being answered, where ``request.host_url`` is known.
"""

from flask import current_app, request


def media_url(key):
    """Absolute url for one file served by the ``/media/<name>`` route."""
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        base = request.host_url.rstrip("/")
    return f"{base}/media/{key}"


def absolute_resource(value):
    """Expand a stored ``media:<file>`` marker into an absolute url."""
    if value and value.startswith("media:"):
        return media_url(value[len("media:"):])
    return value
