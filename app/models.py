"""SQLAlchemy models backing both the API and the dashboard."""

import datetime as dt
import secrets

from flask import current_app
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    inspect,
    String,
    text,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash


class Base(DeclarativeBase):
    """Declarative base for every table."""


def utcnow() -> dt.datetime:
    """Naive UTC "now".

    SQLite stores datetimes as text, so keeping every timestamp naive and in
    UTC avoids mixing offsets into the stored values and lets the API compare
    client millisecond bounds directly against ``create_time``.
    """
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def millis(timestamp: dt.datetime | None = None) -> int:
    """Milliseconds since epoch, which is what the watch client expects."""
    value = timestamp or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return int(value.timestamp() * 1000)


class User(Base):
    """A watch account that may sign in to the moment service."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    watch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    # Hardware chip id (``ro.boot.xtc.chipid``), which the client sends as the
    # ``token`` field of Base-Request-Param.  Empty means "not bound yet", in
    # which case any device presenting this watch id is accepted.
    chip_id: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    # The last chip id we saw for this account, so an admin can bind it with one
    # click instead of typing a value they cannot easily read off the watch.
    last_chip_id: Mapped[str] = mapped_column(String(64), default="", server_default="", nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Banned accounts get ``000007`` on every call (see ``fail_banned``), which
    # the watch shows as "account suspended".  This is deliberately separate
    # from ``is_active``: that one is a plain ``000004`` lock-out.
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    banned_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    tokens: Mapped[list["AuthToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    moments: Mapped[list["Moment"]] = relationship(back_populates="author", cascade="all, delete-orphan")

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    @property
    def effective_name(self) -> str:
        return self.display_name or self.username


class AuthToken(Base):
    """Bearer token issued to a watch account after authentication."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="tokens")

    @staticmethod
    def new_token() -> str:
        return secrets.token_hex(32)

    @property
    def is_valid(self) -> bool:
        if self.revoked:
            return False
        return self.expires_at > utcnow()


class Moment(Base):
    """A published moment (post)."""

    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    moment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    type: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    resource: Mapped[str] = mapped_column(Text, default="")
    resource_id: Mapped[int] = mapped_column(Integer, default=0)
    location: Mapped[str] = mapped_column(String(256), default="")
    emotion_id: Mapped[int] = mapped_column(Integer, default=0)
    permission_type: Mapped[int] = mapped_column(Integer, default=0)
    topic_list_json: Mapped[str] = mapped_column(Text, default="")
    create_time: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    author: Mapped[User] = relationship(back_populates="moments")
    comments: Mapped[list["Comment"]] = relationship(back_populates="moment", cascade="all, delete-orphan")
    likes: Mapped[list["Like"]] = relationship(back_populates="moment", cascade="all, delete-orphan")


class Comment(Base):
    """A comment on a moment."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    moment_id: Mapped[str] = mapped_column(ForeignKey("moments.moment_id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # The comment being replied to; empty for a plain comment.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    reply_comment_id: Mapped[str] = mapped_column(String(64), default="")
    create_time: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    moment: Mapped[Moment] = relationship(back_populates="comments")
    author: Mapped[User] = relationship()
    parent: Mapped["Comment | None"] = relationship(remote_side=[id])


class Like(Base):
    """A like on a moment."""

    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("moment_id", "user_id", name="uq_like_moment_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    moment_id: Mapped[str] = mapped_column(ForeignKey("moments.moment_id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    emotion_id: Mapped[int] = mapped_column(Integer, default=0)
    create_time: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    moment: Mapped[Moment] = relationship(back_populates="likes")
    user: Mapped[User] = relationship()


class ReviewRequest(Base):
    """An appeal filed from the "account suspended" dialog.

    Stored so the dashboard can show them; nothing consumes them automatically.
    """

    __tablename__ = "review_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(128), default="")
    create_time: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)


engine = None
SessionLocal = None

# Columns added after the first release.  ``create_all`` only creates missing
# tables, so an existing database needs these added explicitly.
ADDED_COLUMNS = {
    "users": {
        "chip_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "last_chip_id": "VARCHAR(64) NOT NULL DEFAULT ''",
        "is_banned": "BOOLEAN NOT NULL DEFAULT 0",
        "banned_at": "DATETIME",
    },
    "comments": {
        "parent_id": "INTEGER",
    },
}


def _upgrade_schema(connection) -> None:
    """Add columns that were introduced after a database was first created."""
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table, columns in ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name not in present:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_engine(database_url: str) -> None:
    """Create the engine/session factory and the schema."""
    global engine, SessionLocal
    engine = create_engine(database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _upgrade_schema(connection)


def get_session():
    """Return a new session; callers are responsible for closing it."""
    if SessionLocal is None:
        init_engine(current_app.config["DATABASE_URL"])
    return SessionLocal()
