"""Pydantic models for the REST API and persisted configuration."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.diagnostics.profiles import PROFILES

DEFAULT_GROUPS = ["Vocals", "Speech", "Instruments", "Drums", "Other"]

# Example channel plan used for first start (fully editable by the operator).
DEFAULT_CHANNEL_PLAN = [
    ("Pastor Mic", "speech", "Speech"),
    ("Worship Leader", "lead_vocal", "Vocals"),
    ("Choir Mic", "choir", "Vocals"),
    ("Keyboard", "keyboard", "Instruments"),
    ("Guitar", "acoustic_guitar", "Instruments"),
    ("Bass", "bass", "Instruments"),
    ("Kick", "kick", "Drums"),
    ("Snare", "snare", "Drums"),
]


class ChannelConfig(BaseModel):
    index: int = Field(..., ge=0, le=63, description="0-based device input index")
    name: str = Field(..., min_length=1, max_length=64)
    profile: str = "general"
    group: str = "Other"
    selected: bool = True
    enabled: bool = True

    @property
    def number(self) -> int:
        return self.index + 1

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"unknown profile '{v}'")
        return v

    def to_api(self) -> dict[str, Any]:
        d = self.model_dump()
        d["number"] = self.number
        return d


def default_channels(n: int) -> list[ChannelConfig]:
    out = []
    for i in range(n):
        if i < len(DEFAULT_CHANNEL_PLAN):
            name, prof, group = DEFAULT_CHANNEL_PLAN[i]
        else:
            name, prof, group = f"Input {i + 1}", "general", "Other"
        out.append(ChannelConfig(index=i, name=name, profile=prof, group=group, selected=True, enabled=True))
    return out


class ChannelsUpdate(BaseModel):
    channels: list[ChannelConfig]


class SelectionUpdate(BaseModel):
    selected: list[int] | None = Field(None, description="1-based channel numbers to select (others deselected)")
    select_all: bool | None = None
    deselect_all: bool | None = None
    group: str | None = Field(None, description="select exactly the channels in this group")
    add_group: str | None = Field(None, description="add the channels of this group to the selection")


class GroupsUpdate(BaseModel):
    groups: list[str] = Field(..., min_length=1)


class DeviceChange(BaseModel):
    device_index: int
    sample_rate: int | None = None
    block_size: int | None = None
    channel_count: int | None = Field(None, ge=1, le=64)
    start: bool = True


class DeviceTestRequest(BaseModel):
    channels: int | None = Field(None, ge=1, le=64)
    sample_rate: int | None = None
    duration_s: float = Field(1.0, ge=0.2, le=5.0)


class OperatorNote(BaseModel):
    note: str | None = Field(None, max_length=500)


class SettingsUpdate(BaseModel):
    """Partial update; unknown keys are rejected."""
    values: dict[str, Any]
