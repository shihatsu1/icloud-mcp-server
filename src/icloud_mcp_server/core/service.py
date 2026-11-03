"""Tool logic shared by every backend. Read-only in M1; writes arrive in M4."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..backends.base import CalendarBackend
from ..config import Settings
from . import freebusy
from .changes import (
    CHANGE_KEYS,
    apply_changes,
    build_new_event,
    differences,
    normalize_changes,
    summarize_recurrence,
)
from .errors import CalendarError
from .models import (
    FLOATING,
    CalendarInfo,
    CalendarOut,
    ChangePreview,
    ConfirmResult,
    Event,
    EventDetail,
    EventSummary,
    FreeSlot,
    FreeTimeResult,
    NewEvent,
    SearchResult,
    UntrustedDetail,
    UntrustedSummary,
)
from .timezones import (
    as_aware,
    display_for_event,
    display_for_range,
    get_zone,
    parse_instant,
    parse_range,
)
from .untrusted import MAX_LOCATION, MAX_NOTES, MAX_TITLE, MAX_URL, NOTICE, clean, was_cut
from .writes import OperationRecord, PendingChange, PendingStore

UNTRUSTED_FIELDS = ("title", "location", "notes")  # text in previews that other people can write
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


class CalendarService:
    def __init__(self, backend: CalendarBackend, settings: Settings, store: PendingStore | None = None):
        self.backend = backend
        self.settings = settings
        self.default_tz: ZoneInfo = get_zone(settings.default_timezone)
        self.store = store or PendingStore()

    # ---- conversion -------------------------------------------------------------------------

    def _times(self, event: Event) -> dict:
        """start/end fields in the event's own zone (all-day: plain dates)."""
        if event.all_day:
            return {
                "start": None,
                "end": None,
                "start_date": _iso(event.start.date() if isinstance(event.start, datetime) else event.start),
                "end_date": _iso(event.end.date() if isinstance(event.end, datetime) else event.end),
                "timezone": None,
                "interpreted_in": None,
            }
        start, end = as_aware(event, self.default_tz)
        floating = event.tzid in (None, FLOATING)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "start_date": None,
            "end_date": None,
            "timezone": FLOATING if floating else event.tzid,
            "interpreted_in": self.settings.default_timezone if floating else None,
        }

    def _common(self, event: Event) -> dict:
        return {
            "event_id": event.event_id,
            "occurrence_start": _iso(event.occurrence_start),
            "series_id": event.series_id,
            "is_recurring": event.is_recurring,
            "calendar_id": event.calendar_id,
            "calendar_name": event.calendar_name,
            "all_day": event.all_day,
            "availability": event.availability,
            "display": display_for_event(event, list(self.settings.display_timezones), self.default_tz),
            **self._times(event),
        }

    def summary(self, event: Event) -> EventSummary:
        return EventSummary(
            **self._common(event),
            untrusted=UntrustedSummary(
                title=clean(event.title, MAX_TITLE), location=clean(event.location, MAX_LOCATION)
            ),
        )

    def detail(self, event: Event) -> EventDetail:
        return EventDetail(
            **self._common(event),
            untrusted=UntrustedDetail(
                title=clean(event.title, MAX_TITLE),
                location=clean(event.location, MAX_LOCATION),
                notes=clean(event.notes, MAX_NOTES),
                url=clean(event.url, MAX_URL),
            ),
            recurrence=event.recurrence,
            rrule_text=event.rrule_text,
            exceptions_count=event.exceptions_count,
            attendees=list(event.attendees),
            alarms_minutes=list(event.alarms_minutes),
            last_modified=_iso(event.last_modified),
            version=event.version,
            notice=NOTICE,
        )

    # ---- tools ------------------------------------------------------------------------------

    def _write_block(self, cal: CalendarInfo, all_calendars: list[CalendarInfo]) -> str | None:
        """Why this server will not write to the calendar, or None if it may. Stable IDs beat names."""
        if cal.read_only_in_source is True:
            return "the calendar service reports it read-only for this account"
        if self.settings.writable_calendar_ids:
            if cal.calendar_id not in self.settings.writable_calendar_ids:
                return "not on this server's writable calendar ID list"
            return None
        if cal.calendar_name not in self.settings.writable_calendars:
            return "not on this server's writable calendar list"
        same_name = sum(1 for c in all_calendars if c.calendar_name == cal.calendar_name)
        if same_name > 1:
            return f"{same_name} calendars share this name; list them by ID in WRITABLE_CALENDAR_IDS"
        return None

    def list_calendars(self) -> list[CalendarOut]:
        calendars = self.backend.list_calendars()
        out = []
        for cal in calendars:
            reason = self._write_block(cal, calendars)
            out.append(
                CalendarOut(
                    calendar_id=cal.calendar_id,
                    calendar_name=cal.calendar_name,
                    source=cal.source,
                    color=cal.color,
                    writable_by_server=self.settings.allow_writes and reason is None,
                    read_only_in_source=cal.read_only_in_source,
                    write_blocked_reason=None if not self.settings.allow_writes else reason,
                    default_timezone=cal.default_timezone,
                )
            )
        return out

    def _check_calendar_ids(self, calendar_ids: list[str] | None) -> list[str] | None:
        if not calendar_ids:
            return None
        known = {c.calendar_id for c in self.backend.list_calendars()}
        unknown = [c for c in calendar_ids if c not in known]
        if unknown:
            raise CalendarError("NOT_FOUND", f"Unknown calendar_id: {unknown[0]!r}.")
        return list(calendar_ids)

    def search_events(
        self,
        start: str,
        end: str,
        query: str | None = None,
        calendar_ids: list[str] | None = None,
        include_all_day: bool = True,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> SearchResult:
        r_start, r_end = parse_range(start, end, self.default_tz)
        limit = max(1, min(limit, MAX_LIMIT))
        offset = _decode_cursor(cursor)
        ids = self._check_calendar_ids(calendar_ids)

        events = [
            e
            for e in self.backend.search_events(r_start, r_end, ids)
            if e.status != "cancelled" and (include_all_day or not e.all_day)
        ]
        events = [e for e in events if _overlaps(e, r_start, r_end, self.default_tz)]
        if query and query.strip():
            terms = [t.lower() for t in query.split()]
            events = [e for e in events if all(t in _haystack(e) for t in terms)]
        events.sort(key=lambda e: (as_aware(e, self.default_tz)[0], (e.title or "").lower()))

        page = events[offset : offset + limit]
        more = offset + limit < len(events)
        return SearchResult(
            events=[self.summary(e) for e in page],
            next_cursor=str(offset + limit) if more else None,
            notice=NOTICE,
        )

    def get_event(self, event_id: str, occurrence_start: str | None = None) -> EventDetail:
        occ = None
        if occurrence_start:
            occ = (
                date.fromisoformat(occurrence_start)
                if len(occurrence_start.strip()) == 10
                else parse_instant(occurrence_start, self.default_tz)
            )
        return self.detail(self.backend.get_event(event_id, occ))

    def find_free_time(
        self,
        start: str,
        end: str,
        duration_minutes: int,
        working_hours: dict[str, str] | None = None,
        working_hours_timezone: str | None = None,
        weekdays: list[str] | None = None,
        calendar_ids: list[str] | None = None,
        count_all_day_as_busy: bool = False,
        ignore_free_availability: bool = True,
        overlap_timezone: str | None = None,
        overlap_working_hours: dict[str, str] | None = None,
        max_results: int = 10,
    ) -> FreeTimeResult:
        if not 15 <= duration_minutes <= 480:
            raise CalendarError("INVALID_INPUT", "duration_minutes must be between 15 and 480.")
        r_start, r_end = parse_range(start, end, self.default_tz)
        wh_zone_name = working_hours_timezone or self.settings.default_timezone
        wh_zone = get_zone(wh_zone_name)
        hours = _hours(working_hours)
        days = freebusy.parse_weekdays(weekdays or ["MO", "TU", "WE", "TH", "FR"])
        ids = self._check_calendar_ids(calendar_ids)

        busy: list[freebusy.Interval] = []
        for event in self.backend.search_events(r_start, r_end, ids):
            if event.status == "cancelled":
                continue
            if ignore_free_availability and event.availability == "free":
                continue
            if event.all_day:
                if not count_all_day_as_busy:
                    continue
                busy.append(
                    freebusy.all_day_busy(_as_day(event.start), _as_day(event.end), wh_zone)
                )
            else:
                busy.append(as_aware(event, self.default_tz))

        overlap_zone = get_zone(overlap_timezone) if overlap_timezone else None
        slots = freebusy.find_free(
            r_start,
            r_end,
            busy,
            timedelta(minutes=duration_minutes),
            wh_zone,
            hours,
            days,
            overlap_zone,
            _hours(overlap_working_hours) if overlap_working_hours else None,
            max(1, min(max_results, 50)),
        )
        zones = list(self.settings.display_timezones)
        result = [
            FreeSlot(
                start=s.astimezone(wh_zone).isoformat(),
                end=e.astimezone(wh_zone).isoformat(),
                minutes=int((e - s).total_seconds() // 60),
                display=display_for_range(s, e, zones),
            )
            for s, e in slots
        ]
        note = (
            f"Free windows of at least {duration_minutes} minutes inside {hours[0]:%H:%M}-{hours[1]:%H:%M} "
            f"{wh_zone_name} on {', '.join(weekdays or ['MO', 'TU', 'WE', 'TH', 'FR'])}"
            + (f", also inside the overlap hours in {overlap_timezone}" if overlap_zone else "")
            + ". "
            + ("All-day events count as busy. " if count_all_day_as_busy else "All-day events were ignored. ")
            + "Each window may be longer than the requested duration."
        )
        return FreeTimeResult(slots=result, note=note)

    # ---- writes: preview, then confirm (docs/TOOLS.md sections 5-8) ---------------------------

    def _writable_calendar(self, calendar_id: str) -> CalendarInfo:
        if not self.settings.allow_writes:
            raise CalendarError("WRITES_DISABLED", "This server is running without write access.")
        calendars = self.backend.list_calendars()
        cal = next((c for c in calendars if c.calendar_id == calendar_id), None)
        if cal is None:
            raise CalendarError("NOT_FOUND", f"Unknown calendar_id: {calendar_id!r}.")
        reason = self._write_block(cal, calendars)
        if reason:
            raise CalendarError("CALENDAR_NOT_WRITABLE", f"The calendar {cal.calendar_name!r} cannot be written: {reason}.")
        return cal

    @staticmethod
    def _refuse_guests(*events: Event) -> None:
        """Changing or deleting an event with guests can make iCloud email them, so it is never done here.

        Every write replaces the whole stored resource, so guests or scheduling data on ANY part of the
        series (the master, the targeted occurrence, a detached override) block the operation.
        """
        for event in events:
            if event.attendees or event.scheduling:
                title = clean(event.title, MAX_TITLE)
                has = (
                    f"has {len(event.attendees)} guest(s) or is an invitation"
                    if event.attendees
                    else "has scheduling data (an organizer or invitation)"
                )
                raise CalendarError(
                    "HAS_GUESTS",
                    f"'{title}' {has}. Changing or deleting it could send email to the guests, which this server does not do.",
                )

    @staticmethod
    def _require_version(event: Event) -> None:
        """Fail closed: without a strong version the write could not be made conditional."""
        if not event.version:
            raise CalendarError(
                "VERSION_UNAVAILABLE", f"'{clean(event.title, MAX_TITLE)}' has no reliable version, so it cannot be changed safely."
            )

    def _fields(self, event: Event) -> dict:
        c = self._common(event)
        cut = [
            name
            for name, value, limit in (
                ("title", event.title, MAX_TITLE),
                ("location", event.location, MAX_LOCATION),
                ("notes", event.notes, MAX_NOTES),
            )
            if was_cut(value, limit)
        ]
        out = {
            "title": clean(event.title, MAX_TITLE),
            "location": clean(event.location, MAX_LOCATION),
            "notes": clean(event.notes, MAX_NOTES),
            "all_day": c["all_day"],
            "start": c["start"],
            "end": c["end"],
            "start_date": c["start_date"],
            "end_date": c["end_date"],
            "timezone": c["timezone"],
            "alarms_minutes": list(event.alarms_minutes),
            "recurrence": event.recurrence,
            "display": c["display"],
        }
        if cut:
            out["truncated_for_display"] = cut  # existing text longer than the display limit; never true for new values
        return out

    def _issue(self, preview: dict, change: PendingChange, principal: str | None) -> ChangePreview:
        operation_id = uuid4().hex[:16]
        token, expires = self.store.issue(replace(change, operation_id=operation_id, principal=principal))
        return ChangePreview(
            preview={**preview, "operation_id": operation_id},
            confirm_token=token,
            operation_id=operation_id,
            expires_at=datetime.fromtimestamp(expires, self.default_tz).isoformat(timespec="seconds"),
            notice="Nothing has been written yet. Show this preview to the user and call confirm_change only after they agree. " + NOTICE,
        )

    def _parse_occurrence(self, occurrence_start: str | None) -> date | datetime | None:
        if not occurrence_start:
            return None
        text = occurrence_start.strip()
        try:
            return date.fromisoformat(text) if len(text) == 10 else parse_instant(text, self.default_tz)
        except ValueError:
            raise CalendarError("INVALID_INPUT", "occurrence_start must be a date or an ISO 8601 date-time.") from None

    def _scoped_target(self, event_id: str, occurrence_start: str | None, scope: str | None):
        """Return (master, target, scope, occurrence) or a ChangePreview asking for the scope."""
        if scope not in (None, "this_occurrence", "whole_series"):
            raise CalendarError("INVALID_INPUT", "scope must be 'this_occurrence' or 'whole_series'.")
        occ = self._parse_occurrence(occurrence_start)
        master = self.backend.get_event(event_id, None)
        if not master.is_recurring:
            return master, master, "whole", None
        if scope is None:
            return ChangePreview(
                needs_input="AMBIGUOUS_SCOPE",
                question=(
                    f"'{clean(master.title, MAX_TITLE)}' repeats. Change only one occurrence "
                    "(scope this_occurrence, with occurrence_start) or every occurrence (scope whole_series)?"
                ),
                notice="No confirm_token was issued. Ask the user, then call again with a scope.",
            )
        if scope == "whole_series":
            return master, master, "whole", None
        if occ is None:
            raise CalendarError("INVALID_INPUT", "occurrence_start is required with scope this_occurrence.")
        return master, self.backend.get_event(event_id, occ), "occurrence", occ

    def preview_create(self, calendar_id: str, title: str, *, principal: str | None = None, **fields) -> ChangePreview:
        cal = self._writable_calendar(calendar_id)
        new = build_new_event(
            calendar_id=calendar_id, title=title, default_timezone=self.settings.default_timezone, **fields
        )
        for earlier in self.store.unknown("create"):  # do not make a second copy of a create that may have landed
            prior = earlier.change.payload["new"] if earlier.change else None
            if prior and (prior.calendar_id, prior.title, prior.start, prior.end) == (new.calendar_id, new.title, new.start, new.end):
                raise CalendarError(
                    "OUTCOME_UNKNOWN",
                    f"An earlier attempt to create this same event (operation {earlier.operation_id}) has an unknown outcome. "
                    "Check the calendar, or call confirm_change with that earlier token to re-check, before creating it again.",
                )
        new = replace(new, uid=str(uuid4()).upper())  # fixed now, so a lost answer can be settled by looking it up
        planned_id = f"{calendar_id}~{new.uid}"
        shown = replace(self._planned(new, cal), event_id=planned_id)
        preview = {
            "action": "create",
            "calendar": cal.calendar_name,
            "planned_event_id": planned_id,
            "fields": self._fields(shown),
            "untrusted_fields": list(UNTRUSTED_FIELDS),
            "recurrence_summary": summarize_recurrence(new.recurrence),
        }
        return self._issue(
            preview, PendingChange("create", calendar_id, event_id=planned_id, payload={"new": new, "expected": shown}), principal
        )

    @staticmethod
    def _planned(new: NewEvent, cal: CalendarInfo) -> Event:
        return Event(
            event_id="(new)",
            calendar_id=cal.calendar_id,
            calendar_name=cal.calendar_name,
            all_day=new.all_day,
            start=new.start,
            end=new.end,
            tzid=new.tzid,
            title=new.title,
            location=new.location,
            notes=new.notes,
            is_recurring=new.recurrence is not None,
            recurrence=new.recurrence,
            alarms_minutes=new.alarms_minutes,
        )

    def preview_update(
        self,
        event_id: str,
        changes: dict,
        occurrence_start: str | None = None,
        scope: str | None = None,
        *,
        principal: str | None = None,
    ) -> ChangePreview:
        found = self._scoped_target(event_id, occurrence_start, scope)
        if isinstance(found, ChangePreview):
            return found
        master, target, kind, occ = found
        self._writable_calendar(master.calendar_id)
        self._refuse_guests(master, target)
        self._require_version(master)
        norm = normalize_changes(target, changes, self.default_tz, allow_recurrence=kind == "whole")
        after = apply_changes(target, norm)
        changed = [k for k in CHANGE_KEYS if self._differs(target, after, k, norm)]
        if not changed:
            raise CalendarError("INVALID_INPUT", "Those values are already what the event has; nothing would change.")
        warnings = []
        parts = set(master.extra.get("recurrence_parts", ()))
        if kind == "whole" and norm.get("recurrence") and parts & {"RDATE", "EXRULE"}:
            raise CalendarError(
                "INVALID_INPUT",
                "This series also has extra repeat dates or exclusion rules that this server cannot edit safely. "
                "Remove the repeat entirely, or change it in the Calendar app.",
            )
        removing = kind == "whole" and "recurrence" in norm and norm["recurrence"] is None
        if removing and master.is_recurring:
            overrides = master.extra.get("overrides", master.exceptions_count)
            if overrides:
                raise CalendarError(
                    "INVALID_INPUT",
                    f"This series has {overrides} individually edited occurrence(s); removing the repeat would orphan them. "
                    "Delete or change those occurrences first.",
                )
            if parts:
                warnings.append("Extra repeat dates and skipped dates (" + ", ".join(sorted(parts)) + ") are removed together with the repeat.")
        elif kind == "whole" and master.is_recurring and master.exceptions_count:
            if {"start", "timezone", "recurrence"} & set(changed):
                raise CalendarError(
                    "INVALID_INPUT",
                    f"This series has {master.exceptions_count} individually edited or deleted occurrence(s); changing "
                    "its start, zone or repeat rule would detach them. Change the occurrences one by one, or create a new series.",
                )
            warnings.append(f"{master.exceptions_count} occurrence(s) were edited or removed individually and keep their own values.")
        preview = {
            "action": "update",
            "scope": scope if master.is_recurring else None,
            "calendar": master.calendar_name,
            "event_id": event_id,
            "occurrence_start": _iso(occ),
            "before": self._fields(target),
            "after": self._fields(after),
            "changed_fields": changed,
            "untrusted_fields": list(UNTRUSTED_FIELDS),
            "warnings": warnings,
        }
        change = PendingChange(
            "update",
            master.calendar_id,
            event_id=event_id,
            occurrence_start=_iso(occ),
            scope=kind,
            payload={"changes": norm, "expected": after, "keys": [k for k in norm]},
            version=master.version,
        )
        return self._issue(preview, change, principal)

    @staticmethod
    def _differs(before: Event, after: Event, key: str, norm: dict) -> bool:
        mapping = {"timezone": "tzid"}
        if key == "timezone":
            return "tzid" in norm and before.tzid != after.tzid
        attr = mapping.get(key, key)
        return key in norm and getattr(before, attr) != getattr(after, attr)

    def preview_delete(
        self, event_id: str, occurrence_start: str | None = None, scope: str | None = None, *, principal: str | None = None
    ) -> ChangePreview:
        found = self._scoped_target(event_id, occurrence_start, scope)
        if isinstance(found, ChangePreview):
            return found
        master, target, kind, occ = found
        self._writable_calendar(master.calendar_id)
        self._refuse_guests(master, target)
        self._require_version(master)
        if master.is_recurring and kind == "whole":
            warning = "This deletes the whole series: every occurrence, including past ones."
        elif master.is_recurring:
            warning = "This deletes only this one occurrence; the rest of the series stays."
        else:
            warning = "This deletes the event."
        preview = {
            "action": "delete",
            "scope": scope if master.is_recurring else None,
            "calendar": master.calendar_name,
            "event_id": event_id,
            "occurrence_start": _iso(occ),
            "event": self._fields(target),
            "untrusted_fields": list(UNTRUSTED_FIELDS),
            "warning": warning,
        }
        change = PendingChange(
            "delete", master.calendar_id, event_id=event_id, occurrence_start=_iso(occ), scope=kind, version=master.version
        )
        return self._issue(preview, change, principal)

    def confirm_change(self, confirm_token: str, principal: str | None = None) -> ConfirmResult:
        """Apply an approved change once. A token that was already used is answered from the journal and
        is never run again; an uncertain outcome is settled by reading the calendar, never by writing."""
        if not self.settings.allow_writes:
            raise CalendarError("WRITES_DISABLED", "This server is running without write access.")
        redeemed = self.store.redeem(confirm_token, principal)
        if redeemed.record is not None:
            return self._replay(redeemed.record)
        change = redeemed.change
        assert change is not None
        stage = ["pre"]  # pre: nothing sent | writing: inside the backend write | post: write returned
        try:
            result = self._execute(change, stage)
        except CalendarError as exc:
            return self._failed(change, exc, stage[0])
        self._settle(change, "applied", result.message, event_id=result.event.event_id if result.event else change.event_id)
        return result.model_copy(update={"operation_id": change.operation_id})

    def _settle(self, change: PendingChange, outcome: str, message: str, *, event_id: str | None = None, detail=()) -> OperationRecord:
        record = OperationRecord(
            change.operation_id, change.action, outcome, message, event_id or change.event_id,  # type: ignore[arg-type]
            tuple(detail), change, change.principal, self.store._clock(),
        )
        self.store.settle(record)
        return record

    def _failed(self, change: PendingChange, exc: CalendarError, stage: str) -> ConfirmResult:
        uncertain = (stage == "writing" and exc.code == "OUTCOME_UNKNOWN") or (
            stage == "post" and exc.code != "WRITE_VERIFICATION_FAILED"
        )
        if uncertain:
            return self._result(self._reconcile(change))
        if stage == "post":  # sent and read back, but the calendar shows different values
            self._settle(change, "unknown", exc.message)
            raise exc
        self._settle(change, "not_applied", exc.message)
        raise exc

    def _execute(self, change: PendingChange, stage: list[str]) -> ConfirmResult:
        self._writable_calendar(change.calendar_id)  # the allow-list is checked again at write time
        occ = self._parse_occurrence(change.occurrence_start)

        if change.action == "create":
            expected: Event = change.payload["expected"]
            stage[0] = "writing"
            created = self.backend.create_event(change.payload["new"])
            stage[0] = "post"
            created = self.backend.get_event(created.event_id)  # re-read from the calendar, not the write's own answer
            self._verify(created, expected, ["title", "location", "notes", "start", "end", "tzid", "alarms_minutes", "recurrence"])
            return ConfirmResult(result="applied", action="create", event=self.detail(created), message="The event was created.", notice=NOTICE)

        assert change.event_id is not None
        current = self.backend.get_event(change.event_id, None)
        if change.version is not None and current.version != change.version:
            raise CalendarError("CONFLICT", "The event was changed after the preview, so nothing was written.")
        self._refuse_guests(current)

        if change.action == "update":
            expected = change.payload["expected"]
            stage[0] = "writing"
            self.backend.update_event(
                change.event_id, change.payload["changes"], scope=change.scope or "whole", occurrence_start=occ, expected_version=change.version
            )
            stage[0] = "post"
            actual = self.backend.get_event(change.event_id, occ if change.scope == "occurrence" else None)
            self._verify(actual, expected, list(change.payload["keys"]))
            return ConfirmResult(result="applied", action="update", event=self.detail(actual), message="The event was updated.", notice=NOTICE)

        stage[0] = "writing"
        self.backend.delete_event(change.event_id, scope=change.scope or "whole", occurrence_start=occ, expected_version=change.version)
        stage[0] = "post"
        try:
            still = self.backend.get_event(change.event_id, occ if change.scope == "occurrence" else None)
        except CalendarError as exc:
            if exc.code != "NOT_FOUND":
                raise
        else:
            raise CalendarError(
                "WRITE_VERIFICATION_FAILED",
                f"After the delete the calendar still shows '{clean(still.title, MAX_TITLE)}'. Check the calendar.",
            )
        what = "The whole series was deleted." if change.scope == "whole" and current.is_recurring else (
            "That occurrence was deleted." if change.scope == "occurrence" else "The event was deleted."
        )
        return ConfirmResult(result="applied", action="delete", event=None, message=what, notice=NOTICE)

    # ---- outcomes: reconcile by reading, never by writing again -----------------------------------

    def _reconcile(self, change: PendingChange) -> OperationRecord:
        """One bounded read to learn whether an uncertain write applied."""
        try:
            return self._reconcile_read(change)
        except CalendarError:
            return self._settle(change, "unknown", "The calendar could not be read to check. Do not retry; look at the calendar.")

    def _reconcile_read(self, change: PendingChange) -> OperationRecord:
        occ = self._parse_occurrence(change.occurrence_start)
        assert change.event_id is not None
        if change.action == "create":
            try:
                actual = self.backend.get_event(change.event_id)
            except CalendarError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                return self._settle(
                    change, "unknown",
                    "No event with the planned ID is on the calendar yet, but the request may still arrive. "
                    "Do not create it again; check the calendar first.",
                )
            wrong = differences(actual, change.payload["expected"], ["title", "start", "end", "tzid", "recurrence"], self.default_tz)
            if wrong:
                return self._settle(change, "unknown", "An event with the planned ID exists but differs.", detail=wrong)
            return self._settle(change, "applied", "The event was created (confirmed by reading the calendar).")
        try:
            actual = self.backend.get_event(change.event_id, occ if change.scope == "occurrence" else None)
        except CalendarError as exc:
            if exc.code != "NOT_FOUND":
                raise
            if change.action == "delete":
                return self._settle(change, "applied", "The event was deleted (confirmed by reading the calendar).")
            return self._settle(change, "unknown", "The event is no longer on the calendar. Check it.")
        if change.action == "delete":
            if actual.version == change.version:
                return self._settle(change, "not_applied", "The event is unchanged, so the delete did not happen.")
            return self._settle(change, "unknown", "The event was changed since the preview. Check it.")
        wrong = differences(actual, change.payload["expected"], list(change.payload["keys"]), self.default_tz)
        if not wrong and actual.version != change.version:
            return self._settle(change, "applied", "The event was updated (confirmed by reading the calendar).", event_id=change.event_id)
        if actual.version == change.version:
            return self._settle(change, "not_applied", "The event is unchanged, so the update did not happen.")
        return self._settle(change, "unknown", "The event differs from the preview. Check it.", detail=wrong)

    def _result(self, record: OperationRecord, *, replayed: bool = False) -> ConfirmResult:
        event = None
        if record.outcome == "applied" and record.action != "delete" and record.event_id:
            try:
                occ = None
                if record.change and record.change.scope == "occurrence":
                    occ = self._parse_occurrence(record.change.occurrence_start)
                event = self.detail(self.backend.get_event(record.event_id, occ))
            except CalendarError:
                event = None
        message = record.message
        if record.outcome == "unknown":
            message += " (operation " + record.operation_id + "; do not retry.)"
        return ConfirmResult(
            result=record.outcome, action=record.action, operation_id=record.operation_id, replayed=replayed,
            detail=list(record.detail), event=event, message=message, notice=NOTICE,
        )

    def _replay(self, record: OperationRecord) -> ConfirmResult:
        if record.outcome == "in_progress":
            return ConfirmResult(
                result="unknown", action=record.action, operation_id=record.operation_id, replayed=True,
                message="This change is still being applied. Do not retry; ask again in a moment.", notice=NOTICE,
            )
        if record.outcome == "unknown" and record.change is not None:
            record = self._reconcile(record.change)  # one read; the change itself is never run again
        return self._result(record, replayed=True)

    def _verify(self, actual: Event, expected: Event, keys: list[str]) -> None:
        wrong = differences(actual, expected, keys, self.default_tz)
        if wrong:
            raise CalendarError(
                "WRITE_VERIFICATION_FAILED",
                f"After the change the calendar shows different values for: {', '.join(wrong)}. "
                f"Actual: {self._fields(actual)}",
            )


# ---- helpers ----------------------------------------------------------------------------------


def _as_day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _hours(value: dict[str, str] | None):
    value = value or {"start": "09:00", "end": "18:00"}
    return freebusy.parse_hhmm(value.get("start", "09:00")), freebusy.parse_hhmm(value.get("end", "18:00"))


def _overlaps(event: Event, start: datetime, end: datetime, default_tz: ZoneInfo) -> bool:
    e_start, e_end = as_aware(event, default_tz)
    return e_end > start and e_start < end


def _haystack(event: Event) -> str:
    return " ".join(filter(None, [event.title, event.notes, event.location])).lower()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except ValueError as exc:
        raise CalendarError("INVALID_INPUT", "cursor is not valid; use the next_cursor from the last result.") from exc
    if value < 0:
        raise CalendarError("INVALID_INPUT", "cursor is not valid.")
    return value

