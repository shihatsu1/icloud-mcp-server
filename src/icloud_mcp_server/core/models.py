"""Domain model returned by backends, and the output models returned by tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import BaseModel, Field

FLOATING = "floating"


@dataclass(frozen=True)
class CalendarInfo:
    calendar_id: str
    calendar_name: str
    source: str = "iCloud"
    color: str | None = None
    read_only_in_source: bool = False
    default_timezone: str | None = None


@dataclass(frozen=True)
class Event:
    """One event, or one occurrence of a recurring series, as a backend reports it.

    All-day events use dates with an exclusive end. Timed events use datetimes: aware ones carry
    their own zone; naive ones are floating (tzid == "floating").
    """

    event_id: str
    calendar_id: str
    calendar_name: str
    all_day: bool
    start: date | datetime
    end: date | datetime
    tzid: str | None  # IANA name, "floating", or None for all-day
    title: str | None = None
    location: str | None = None
    notes: str | None = None
    url: str | None = None
    availability: str = "busy"  # busy | free | tentative | unavailable
    status: str = "confirmed"  # confirmed | tentative | cancelled
    series_id: str | None = None
    is_recurring: bool = False
    occurrence_start: date | datetime | None = None
    recurrence: dict | None = None
    rrule_text: str | None = None
    exceptions_count: int = 0
    attendees: tuple[str, ...] = ()
    alarms_minutes: tuple[int, ...] = ()
    last_modified: datetime | None = None
    version: str | None = None
    extra: dict = field(default_factory=dict)


class CalendarOut(BaseModel):
    calendar_id: str
    calendar_name: str
    source: str
    color: str | None = None
    writable_by_server: bool
    read_only_in_source: bool
    default_timezone: str | None = None


class CalendarList(BaseModel):
    calendars: list[CalendarOut]


class UntrustedSummary(BaseModel):
    title: str | None = None
    location: str | None = None


class EventSummary(BaseModel):
    event_id: str
    occurrence_start: str | None = None
    series_id: str | None = None
    is_recurring: bool
    calendar_id: str
    calendar_name: str
    all_day: bool
    start: str | None = Field(default=None, description="ISO 8601 with offset; null for all-day events")
    end: str | None = None
    start_date: str | None = Field(default=None, description="YYYY-MM-DD, all-day events only")
    end_date: str | None = Field(default=None, description="YYYY-MM-DD exclusive, all-day events only")
    timezone: str | None = Field(
        default=None, description="IANA zone; 'floating' if the event has no zone; null for all-day"
    )
    interpreted_in: str | None = Field(
        default=None, description="For floating times: the zone the times above were read in"
    )
    availability: str
    display: dict[str, str]
    untrusted: UntrustedSummary


class SearchResult(BaseModel):
    events: list[EventSummary]
    next_cursor: str | None = None
    notice: str


class UntrustedDetail(UntrustedSummary):
    notes: str | None = None
    url: str | None = None


class EventDetail(BaseModel):
    event_id: str
    occurrence_start: str | None = None
    series_id: str | None = None
    is_recurring: bool
    calendar_id: str
    calendar_name: str
    all_day: bool
    start: str | None = None
    end: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    timezone: str | None = None
    interpreted_in: str | None = None
    availability: str
    display: dict[str, str]
    untrusted: UntrustedDetail
    recurrence: dict | None = None
    rrule_text: str | None = None
    exceptions_count: int = 0
    attendees: list[str] = []
    alarms_minutes: list[int] = []
    last_modified: str | None = None
    version: str | None = None
    notice: str


class FreeSlot(BaseModel):
    start: str
    end: str
    minutes: int
    display: dict[str, str]


class FreeTimeResult(BaseModel):
    slots: list[FreeSlot]
    note: str
