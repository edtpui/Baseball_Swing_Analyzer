"""Swing history and settings on disk.

Each swing is two files: `<id>.bin` holds the raw capture exactly as the sensor
sent it, `<id>.json` holds the processed result. Keeping the raw capture means
swings can be re-analysed when the bat dimensions change.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, model_validator

from .processing import BatConfig

log = logging.getLogger(__name__)

_ID_PATTERN = re.compile(r"^\d{8}T\d{6}-\d{5}(-\d+)?$")


class Settings(BaseModel):
    bat_length_m: float = Field(0.838, ge=0.5, le=1.1)
    sweet_spot_from_tip_m: float = Field(0.152, ge=0.05, le=0.3)
    sensor_faces_out: bool = True
    start_dps: int = Field(500, ge=200, le=2000)
    min_peak_dps: int = Field(900, ge=300, le=3500)

    @model_validator(mode="after")
    def _peak_above_start(self) -> "Settings":
        if self.min_peak_dps < self.start_dps + 100:
            raise ValueError("min_peak_dps must be at least 100 dps above start_dps")
        return self

    def bat(self) -> BatConfig:
        return BatConfig(
            length_m=self.bat_length_m,
            sweet_spot_from_tip_m=self.sweet_spot_from_tip_m,
            sensor_faces_out=self.sensor_faces_out,
        )


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.current = self._load()

    def _load(self) -> Settings:
        if not self._path.exists():
            return Settings()
        try:
            return Settings.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (ValidationError, OSError) as exc:
            log.warning("ignoring unreadable settings file %s: %s", self._path, exc)
            return Settings()

    def save(self, settings: Settings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        self.current = settings


def _summary(record: dict) -> dict:
    return {
        "id": record["id"],
        "recorded_at": record["recorded_at"],
        "device_swing_id": record["device_swing_id"],
        "metrics": record["metrics"],
        "confidence": record["quality"]["confidence"],
    }


class SwingStore:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict] = {}
        for path in sorted(self._dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                self._index[record["id"]] = _summary(record)
            except (OSError, ValueError, KeyError) as exc:
                log.warning("skipping unreadable swing file %s: %s", path.name, exc)

    def _paths(self, swing_id: str) -> tuple[Path, Path]:
        if not _ID_PATTERN.match(swing_id):
            raise KeyError(swing_id)
        return self._dir / f"{swing_id}.json", self._dir / f"{swing_id}.bin"

    def add(self, blob: bytes, result: dict, device_swing_id: int) -> dict:
        now = datetime.now().astimezone()
        base = f"{now:%Y%m%dT%H%M%S}-{device_swing_id:05d}"
        swing_id, suffix = base, 1
        while swing_id in self._index:
            suffix += 1
            swing_id = f"{base}-{suffix}"
        record = {
            "id": swing_id,
            "recorded_at": now.isoformat(timespec="seconds"),
            "device_swing_id": device_swing_id,
            **result,
        }
        json_path, bin_path = self._paths(swing_id)
        bin_path.write_bytes(blob)
        json_path.write_text(json.dumps(record), encoding="utf-8")
        self._index[swing_id] = _summary(record)
        return record

    def list(self) -> list[dict]:
        return sorted(self._index.values(), key=lambda s: s["id"], reverse=True)

    def get(self, swing_id: str) -> dict:
        json_path, _ = self._paths(swing_id)
        if swing_id not in self._index:
            raise KeyError(swing_id)
        return json.loads(json_path.read_text(encoding="utf-8"))

    def raw(self, swing_id: str) -> bytes:
        _, bin_path = self._paths(swing_id)
        if swing_id not in self._index:
            raise KeyError(swing_id)
        return bin_path.read_bytes()

    def replace_result(self, swing_id: str, result: dict) -> None:
        record = self.get(swing_id)
        record.update(result)
        json_path, _ = self._paths(swing_id)
        json_path.write_text(json.dumps(record), encoding="utf-8")
        self._index[swing_id] = _summary(record)

    def delete(self, swing_id: str) -> None:
        json_path, bin_path = self._paths(swing_id)
        if swing_id not in self._index:
            raise KeyError(swing_id)
        json_path.unlink(missing_ok=True)
        bin_path.unlink(missing_ok=True)
        del self._index[swing_id]
