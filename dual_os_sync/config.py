"""Configuration model — parses and validates config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


DRIVE_LETTERS = ("c", "d", "e")


class AccessConfig(BaseModel):
    """Mount-point mappings for cross-OS filesystem access."""

    linux_mount_win_c: str = "/mnt/c"
    linux_mount_win_d: str = "/mnt/d"
    linux_mount_win_e: str = "/mnt/e"
    win_mount_linux_root: Optional[str] = None

    def _mount_map(self) -> dict[str, str]:
        """Build drive-letter → mount-path mapping from ``linux_mount_win_*`` fields."""
        return {
            attr[-1]: getattr(self, attr)
            for attr in ("linux_mount_win_c", "linux_mount_win_d", "linux_mount_win_e")
            if hasattr(self, attr) and getattr(self, attr) is not None
        }

    def drive_mappings(self) -> list[BaseMapping]:
        """Generate drive-level base mappings for content replacement.

        These act as fallbacks so paths like ``C:\\Users\\30413`` get replaced
        even when no explicit ``base_mapping`` covers them.
        """
        result: list[BaseMapping] = []
        for letter in DRIVE_LETTERS:
            mount = getattr(self, f"linux_mount_win_{letter}", None)
            if not mount:
                continue
            win_prefix = f"{letter.upper()}:\\"
            linux_prefix = str(Path(mount)) + "/"
            try:
                result.append(BaseMapping(
                    id=f"_drive_{letter}",
                    win=win_prefix,
                    linux=linux_prefix,
                ))
            except Exception:
                continue
        return result

    def resolve_win_path_on_linux(self, win_path: str) -> Path:
        """Translate a Windows absolute path to its Linux mount location."""
        drive = win_path[0].lower()
        rest = win_path[2:].replace("\\", "/").lstrip("/")
        mount_map = self._mount_map()
        mount = mount_map.get(drive, f"/mnt/{drive}")
        return Path(mount) / rest

    def resolve_linux_path_on_windows(self, linux_path: str) -> Optional[Path]:
        """Translate a Linux absolute path to its Windows mount location."""
        if not self.win_mount_linux_root:
            return None
        return Path(self.win_mount_linux_root) / linux_path.lstrip("/")


class BaseMapping(BaseModel):
    """A bidirectional path mapping rule (e.g. E:\\DL\\ <-> /home/.../.data2/DL/)."""

    id: str
    win: str
    linux: str
    linux_mount_win_e: Optional[str] = "/mnt/e"

    @field_validator("win")
    @classmethod
    def _win_must_end_with_sep(cls, v: str) -> str:
        if not v.endswith("\\"):
            raise ValueError(f"Windows base path must end with '\\': {v!r}")
        return v

    @field_validator("linux")
    @classmethod
    def _linux_must_end_with_sep(cls, v: str) -> str:
        if not v.endswith("/"):
            raise ValueError(f"Linux base path must end with '/': {v!r}")
        return v


class TaskConfig(BaseModel):
    """A single sync task."""

    name: str
    type: str = "two_way_sync"
    depends_on: Optional[str] = None  # id of the base_mapping to use for path replacement
    win_path: str
    linux_path: str
    regex_replace: bool = True
    target_extensions: list[str] = Field(default_factory=lambda: [".jsonl", ".json"])


class Settings(BaseModel):
    """Global settings."""

    sync_strategy: str = "auto_newest"
    state_file_path: str = "~/.config/dual_os_sync/sync_state.json"


class AppConfig(BaseModel):
    """Root configuration object."""

    settings: Settings = Field(default_factory=Settings)
    access: AccessConfig = Field(default_factory=AccessConfig)
    base_mappings: list[BaseMapping] = Field(default_factory=list)
    tasks: list[TaskConfig] = Field(default_factory=list)

    def get_mapping_by_id(self, mapping_id: str) -> BaseMapping:
        """Look up a base mapping by its id."""
        for m in self.base_mappings:
            if m.id == mapping_id:
                return m
        raise KeyError(f"Base mapping not found: {mapping_id!r}")

    def get_mappings_for_task(self, task: TaskConfig) -> list[BaseMapping]:
        """Return the base mappings applicable to a task.

        Drive-level mappings from ``access`` config are appended as fallback
        so that paths on unmapped drives (e.g. ``C:\\Users\\…``) still get
        replaced during content processing.
        """
        result: list[BaseMapping] = []
        if task.depends_on:
            result.append(self.get_mapping_by_id(task.depends_on))
        else:
            result.extend(self.base_mappings)
        result.extend(self.access.drive_mappings())
        return result

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        """Load and validate configuration from a YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)
