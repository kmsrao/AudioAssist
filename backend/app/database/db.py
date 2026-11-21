"""SQLite storage via SQLAlchemy.

Schema management: ``Base.metadata.create_all`` creates missing tables and a
``schema_version`` row records the version. ``MIGRATIONS`` holds ordered,
idempotent SQL steps for future upgrades (applied when the stored version is
older than ``SCHEMA_VERSION``). No raw audio is ever stored.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import (Boolean, Float, ForeignKey, Integer, String, Text, create_engine, event, select, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.logging.setup import get_logger

log = get_logger("database")

SCHEMA_VERSION = 1
MIGRATIONS: dict[int, list[str]] = {
    # 2: ["ALTER TABLE ... ADD COLUMN ..."],
}


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)


class SettingRow(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON


class DeviceConfigRow(Base):
    __tablename__ = "device_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256))
    hostapi: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_rate: Mapped[int] = mapped_column(Integer)
    block_size: Mapped[int] = mapped_column(Integer)
    channel_count: Mapped[int] = mapped_column(Integer)
    last_used: Mapped[float] = mapped_column(Float)


class ChannelRow(Base):
    __tablename__ = "channels"
    index: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0-based device input index
    name: Mapped[str] = mapped_column(String(128))
    profile: Mapped[str] = mapped_column(String(64))
    group: Mapped[str] = mapped_column(String(64))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DiagnosticRow(Base):
    __tablename__ = "diagnostic_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel: Mapped[int] = mapped_column(Integer, index=True)
    channel_name: Mapped[str] = mapped_column(String(128))
    profile: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    first_seen: Mapped[float] = mapped_column(Float, index=True)
    last_seen: Mapped[float] = mapped_column(Float)
    cleared_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    suppressed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation: Mapped[str] = mapped_column(Text)
    data: Mapped[str] = mapped_column(Text)  # JSON: full diagnostic


class RecommendationRow(Base):
    __tablename__ = "recommendation_history"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    diagnostic_id: Mapped[str] = mapped_column(String(32))
    channel: Mapped[int] = mapped_column(Integer, index=True)
    channel_name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[float] = mapped_column(Float, index=True)
    updated_at: Mapped[float] = mapped_column(Float)
    data: Mapped[str] = mapped_column(Text)  # JSON: full recommendation


class OperatorActionRow(Base):
    __tablename__ = "operator_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[float] = mapped_column(Float, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recommendation_id: Mapped[str | None] = mapped_column(ForeignKey("recommendation_history.id"), nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")


class Database:
    def __init__(self, path: Path | str):
        url = "sqlite://" if str(path) == ":memory:" else f"sqlite:///{Path(path).as_posix()}"
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if str(path) == ":memory:":
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.lock = threading.Lock()  # serialise writers (analysis thread + API)
        self._init_schema()

    def _init_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.Session() as s, s.begin():
            row = s.get(SchemaVersion, 1)
            if row is None:
                s.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
                log.info("database initialised", extra={"schema_version": SCHEMA_VERSION})
                return
            for v in range(row.version + 1, SCHEMA_VERSION + 1):
                for stmt in MIGRATIONS.get(v, []):
                    s.execute(text(stmt))
                log.info("database migrated", extra={"to_version": v})
            row.version = SCHEMA_VERSION

    def session(self) -> Session:
        return self.Session()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default, separators=(",", ":"))


def _default(o: Any) -> Any:
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:  # noqa: BLE001
        pass
    return str(o)


__all__ = ["Database", "SettingRow", "DeviceConfigRow", "ChannelRow", "DiagnosticRow", "RecommendationRow",
           "OperatorActionRow", "dumps", "select"]
