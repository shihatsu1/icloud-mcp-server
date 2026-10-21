"""Settings come from environment variables. Secrets are never read here and never logged."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .core.timezones import get_zone

DEFAULT_TZ = "Asia/Tokyo"
DEFAULT_DISPLAY = ("Asia/Tokyo", "Asia/Manila")


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    default_timezone: str = DEFAULT_TZ
    display_timezones: tuple[str, ...] = DEFAULT_DISPLAY
    allow_writes: bool = False
    writable_calendars: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        get_zone(self.default_timezone)
        for name in self.display_timezones:
            get_zone(name)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            default_timezone=env.get("DEFAULT_TIMEZONE", DEFAULT_TZ),
            display_timezones=_csv(env.get("DISPLAY_TIMEZONES")) or DEFAULT_DISPLAY,
            allow_writes=env.get("ALLOW_WRITES", "").lower() in {"1", "true", "yes"},
            writable_calendars=frozenset(_csv(env.get("WRITABLE_CALENDARS"))),
        )
