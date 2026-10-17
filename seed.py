"""Populate the database with the default 心情/状态 templates and topics.

Run once after the first start:  ``python seed.py``

It writes the icons into ``MEDIA_DIR`` and inserts the matching
``MomentTemplate``/``Topic`` rows, so the watch's mood/state picker and the
「小括号」 list both have something to show.
"""

import os
import sys
from pathlib import Path

# ``app.config`` reads the environment at import time, so this must be set
# before the app package is imported below.  Seeding from the shell is a
# one-off maintenance action, not a production listener.
os.environ.setdefault("MOMENT_ALLOW_INSECURE_DEFAULTS", "1")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import create_app  # noqa: E402
from app.models import MomentTemplate, Topic, get_session  # noqa: E402

# Bundled open-source emoji font (Noto Color Emoji, OFL-1.1).  Shipping it with
# the app keeps the generated icons identical on Windows and inside the
# container, where no system emoji font exists.
EMOJI_FONT = str(Path(__file__).resolve().parent / "assets" / "fonts" / "NotoColorEmoji.ttf")
# Noto Color Emoji only ships a 109px bitmap strike, so the size is fixed and
# the icon is scaled afterwards instead of asking FreeType for another size.
EMOJI_STRIKE_SIZE = 109
ICON_SIZE = 108

# (type, emoji, 文案)
MOODS = [
    (0, "\U0001F60A", "今天心情不错"),
    (0, "\U0001F602", "笑死我了"),
    (0, "\U0001F60D", "好喜欢呀"),
    (0, "\U0001F614", "有点难过"),
    (0, "\U0001F621", "气死我了"),
    (0, "\U0001F634", "困死了"),
    (0, "\U0001F62D", "太难了"),
    (0, "\U0001F914", "让我想想"),
    (1, "\U0001F3C3", "正在运动"),
    (1, "\U0001F4DA", "正在学习"),
    (1, "\U0001F634", "已经睡了"),
    (1, "\U0001F3AE", "正在玩耍"),
    (1, "\U0001F374", "正在吃饭"),
    (1, "\U0001F6B4", "在路上"),
]

TOPICS = [
    ("每日打卡", 100),
    ("今天吃什么", 90),
    ("我的周末", 80),
    ("运动打卡", 70),
    ("心情日记", 60),
]


def draw_icon(path: Path, emoji: str) -> None:
    """Render one emoji into a square PNG the watch can load."""
    # Noto Color Emoji is a bitmap font with a single 109px strike; render at
    # that size and let Pillow scale the finished icon down.
    canvas = Image.new("RGBA", (EMOJI_STRIKE_SIZE * 2, EMOJI_STRIKE_SIZE * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(EMOJI_FONT, EMOJI_STRIKE_SIZE)
    left, top, right, bottom = draw.textbbox((0, 0), emoji, font=font, embedded_color=True)
    draw.text(
        ((canvas.width - (right - left)) / 2 - left, (canvas.height - (bottom - top)) / 2 - top),
        emoji,
        font=font,
        embedded_color=True,
    )
    image = canvas.crop(canvas.getbbox()).resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    image.save(path, "PNG")


def seed_database(app):
    """Insert the defaults into an existing app; returns per-table counts.

    Safe to call on every start: tables that already have rows are left
    untouched, so an operator's edits are never overwritten.
    """
    media_dir = Path(app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    session = get_session()
    counts = {}
    try:
        if session.query(MomentTemplate).count() == 0:
            for index, (moment_type, emoji, content) in enumerate(MOODS):
                name = f"template_default_{index}.png"
                draw_icon(media_dir / name, emoji)
                session.add(
                    MomentTemplate(
                        type=moment_type,
                        content=content,
                        resource=f"media:{name}",
                        resource_id=index + 1,
                        sort_value=len(MOODS) - index,
                    )
                )
            counts["templates"] = len(MOODS)

        if session.query(Topic).count() == 0:
            for name, sort_value in TOPICS:
                session.add(Topic(topic_name=name, sort_value=sort_value))
            counts["topics"] = len(TOPICS)

        session.commit()
        return counts
    finally:
        session.close()


def main() -> None:
    app = create_app()
    counts = seed_database(app)
    if counts:
        print(f"inserted {counts}")
    else:
        print("templates and topics already present, nothing to do")


if __name__ == "__main__":
    main()
