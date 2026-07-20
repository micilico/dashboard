from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class TorrentAction(BaseModel):
    hash: str = Field(..., min_length=40, max_length=64)


class TorrentHashesAction(BaseModel):
    hashes: list[str] = Field(default_factory=list, min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def accept_single_hash(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("hash") and not data.get("hashes"):
            return {**data, "hashes": [data["hash"]]}
        return data


class DeleteTorrent(TorrentHashesAction):
    deleteFiles: bool = False


class ForceStartTorrent(TorrentHashesAction):
    enabled: bool = True


class AddMagnet(BaseModel):
    magnet: str | None = Field(default=None, max_length=65535)
    magnets: list[str] = Field(default_factory=list, max_length=50)
    category: str = Field(default="", max_length=80)
    tags: str = Field(default="", max_length=200)
    paused: bool = False
    savePath: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def collect_magnets(self) -> "AddMagnet":
        collected: list[str] = []
        if self.magnet:
            collected.extend(self.magnet.splitlines())
        collected.extend(self.magnets)
        self.magnets = [item.strip() for item in collected if item.strip()]
        return self


class RetryMediaWorkflow(BaseModel):
    scope: str = Field(default="full", pattern="^(full|jellyfin)$")


class ManualMediaActionResult(BaseModel):
    status: str
    message: str


class NotificationAction(BaseModel):
    code: str = Field(..., min_length=3, max_length=160)


class TorrentCategoryUpdate(TorrentHashesAction):
    category: str = Field(default="", max_length=80)


class TorrentTagsUpdate(TorrentHashesAction):
    tags: str = Field(default="", max_length=200)


class TorrentRateLimitUpdate(TorrentHashesAction):
    limitKiB: int = Field(..., ge=0, le=10_000_000)


class TorrentSequentialUpdate(TorrentHashesAction):
    enabled: bool = True


class AutomationRulePayload(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    trigger: str = Field(..., min_length=3, max_length=80)
    conditions: list[str] = Field(default_factory=list, max_length=12)
    actions: list[str] = Field(default_factory=list, min_length=1, max_length=12)
    enabled: bool = False
