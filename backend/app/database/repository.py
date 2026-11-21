"""Persistence operations used by the services."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, desc, select

from app.config.settings import AnalysisSettings
from app.database.db import (ChannelRow, Database, DeviceConfigRow, DiagnosticRow, OperatorActionRow,
                             RecommendationRow, SettingRow, dumps)
from app.logging.setup import get_logger
from app.models.schemas import DEFAULT_GROUPS, ChannelConfig
from app.utils.timeutil import now

log = get_logger("repository")

SETTINGS_KEY = "analysis_settings"
GROUPS_KEY = "channel_groups"


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ settings
    def load_settings(self) -> AnalysisSettings:
        with self.db.session() as s:
            row = s.get(SettingRow, SETTINGS_KEY)
        if row is None:
            return AnalysisSettings()
        try:
            stored = json.loads(row.value)
            known = {k: v for k, v in stored.items() if k in AnalysisSettings.model_fields}
            return AnalysisSettings(**known)
        except Exception as exc:  # noqa: BLE001 - corrupted/old settings: fall back to defaults
            log.warning("stored settings invalid, using defaults", extra={"error": str(exc)})
            return AnalysisSettings()

    def save_settings(self, settings: AnalysisSettings) -> None:
        self._put(SETTINGS_KEY, settings.model_dump())

    def _put(self, key: str, value: Any) -> None:
        with self.db.lock, self.db.session() as s, s.begin():
            row = s.get(SettingRow, key)
            if row is None:
                s.add(SettingRow(key=key, value=dumps(value)))
            else:
                row.value = dumps(value)

    def _get(self, key: str, default: Any) -> Any:
        with self.db.session() as s:
            row = s.get(SettingRow, key)
        return default if row is None else json.loads(row.value)

    def load_groups(self) -> list[str]:
        return list(self._get(GROUPS_KEY, DEFAULT_GROUPS))

    def save_groups(self, groups: list[str]) -> None:
        self._put(GROUPS_KEY, groups)

    # ------------------------------------------------------------------ devices
    def remember_device(self, name: str, hostapi: str | None, index: int | None, sample_rate: int, block_size: int,
                        channel_count: int) -> None:
        with self.db.lock, self.db.session() as s, s.begin():
            row = s.scalars(select(DeviceConfigRow).where(DeviceConfigRow.name == name,
                                                          DeviceConfigRow.hostapi == hostapi)).first()
            if row is None:
                row = DeviceConfigRow(name=name, hostapi=hostapi)
                s.add(row)
            row.device_index, row.sample_rate, row.block_size = index, sample_rate, block_size
            row.channel_count, row.last_used = channel_count, now()

    def device_history(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(select(DeviceConfigRow).order_by(desc(DeviceConfigRow.last_used)).limit(limit)).all()
        return [{"name": r.name, "hostapi": r.hostapi, "device_index": r.device_index, "sample_rate": r.sample_rate,
                 "block_size": r.block_size, "channel_count": r.channel_count, "last_used": r.last_used} for r in rows]

    # ------------------------------------------------------------------ channels
    def load_channels(self) -> list[ChannelConfig]:
        with self.db.session() as s:
            rows = s.scalars(select(ChannelRow).order_by(ChannelRow.index)).all()
        out = []
        for r in rows:
            try:
                out.append(ChannelConfig(index=r.index, name=r.name, profile=r.profile, group=r.group,
                                         selected=r.selected, enabled=r.enabled))
            except Exception:  # noqa: BLE001 - e.g. a profile that no longer exists
                out.append(ChannelConfig(index=r.index, name=r.name, group=r.group, selected=r.selected,
                                         enabled=r.enabled))
        return out

    def save_channels(self, channels: list[ChannelConfig]) -> None:
        with self.db.lock, self.db.session() as s, s.begin():
            s.execute(delete(ChannelRow))
            for c in channels:
                s.add(ChannelRow(index=c.index, name=c.name, profile=c.profile, group=c.group,
                                 selected=c.selected, enabled=c.enabled))

    # ------------------------------------------------------------------ diagnostics
    def upsert_diagnostic(self, d: dict[str, Any]) -> None:
        with self.db.lock, self.db.session() as s, s.begin():
            row = s.get(DiagnosticRow, d["id"])
            if row is None:
                row = DiagnosticRow(id=d["id"])
                s.add(row)
            row.channel, row.channel_name, row.profile = d["channel"], d["channel_name"], d["profile"]
            row.category, row.severity, row.confidence = d["category"], d["severity"], float(d["confidence"])
            row.first_seen, row.last_seen, row.cleared_at = d["first_seen"], d["last_seen"], d.get("cleared_at")
            row.status, row.suppressed_by, row.explanation = d["status"], d.get("suppressed_by"), d["explanation"]
            row.data = dumps(d)

    def diagnostic_history(self, channel: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = select(DiagnosticRow).order_by(desc(DiagnosticRow.first_seen)).limit(limit)
        if channel is not None:
            q = q.where(DiagnosticRow.channel == channel)
        with self.db.session() as s:
            rows = s.scalars(q).all()
        return [json.loads(r.data) | {"status": r.status, "cleared_at": r.cleared_at} for r in rows]

    # ------------------------------------------------------------------ recommendations
    def upsert_recommendation(self, rec: dict[str, Any]) -> None:
        with self.db.lock, self.db.session() as s, s.begin():
            row = s.get(RecommendationRow, rec["id"])
            if row is None:
                row = RecommendationRow(id=rec["id"])
                s.add(row)
            row.diagnostic_id, row.channel, row.channel_name = rec["diagnostic_id"], rec["channel"], rec["channel_name"]
            row.category, row.status = rec["category"], rec["status"]
            row.created_at, row.updated_at, row.data = rec["created_at"], rec["updated_at"], dumps(rec)

    def recommendation_history(self, channel: int | None = None, status: str | None = None,
                               limit: int = 200) -> list[dict[str, Any]]:
        q = select(RecommendationRow).order_by(desc(RecommendationRow.created_at)).limit(limit)
        if channel is not None:
            q = q.where(RecommendationRow.channel == channel)
        if status is not None:
            q = q.where(RecommendationRow.status == status)
        with self.db.session() as s:
            rows = s.scalars(q).all()
        return [json.loads(r.data) for r in rows]

    def get_recommendation(self, rec_id: str) -> dict[str, Any] | None:
        with self.db.session() as s:
            row = s.get(RecommendationRow, rec_id)
        return None if row is None else json.loads(row.data)

    # ------------------------------------------------------------------ operator actions
    def log_action(self, action: str, target: str | None = None, recommendation_id: str | None = None,
                   payload: dict[str, Any] | None = None) -> None:
        with self.db.lock, self.db.session() as s, s.begin():
            s.add(OperatorActionRow(at=now(), action=action, target=target, recommendation_id=recommendation_id,
                                    payload=dumps(payload or {})))

    def actions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.session() as s:
            rows = s.scalars(select(OperatorActionRow).order_by(desc(OperatorActionRow.at)).limit(limit)).all()
        return [{"at": r.at, "action": r.action, "target": r.target, "recommendation_id": r.recommendation_id,
                 "payload": json.loads(r.payload)} for r in rows]
