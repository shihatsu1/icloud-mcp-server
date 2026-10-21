from datetime import UTC, date, datetime, timedelta

from icalendar import Calendar

from icloud_mcp_server.backends.ical_convert import component_to_event, summarize_rrule

HEAD = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n"
TAIL = "END:VCALENDAR\n"


def convert(vevent: str, **kw):
    cal = Calendar.from_ical(HEAD + vevent + TAIL)
    comp = next(c for c in cal.walk() if c.name == "VEVENT")
    return component_to_event(comp, calendar_id="c", calendar_name="Cal", event_id="e1", **kw)


def test_timed_event_with_iana_tzid():
    ev = convert(
        "BEGIN:VEVENT\nUID:u1\nDTSTART;TZID=Asia/Manila:20261022T100000\n"
        "DTEND;TZID=Asia/Manila:20261022T110000\nSUMMARY:Client sync\nLOCATION:Room 1\nEND:VEVENT\n"
    )
    assert ev.tzid == "Asia/Manila" and not ev.all_day
    assert ev.start == datetime(2026, 10, 22, 10, 0, tzinfo=ev.start.tzinfo)
    assert ev.start.utcoffset() == timedelta(hours=8)
    assert (ev.title, ev.location, ev.availability, ev.status) == ("Client sync", "Room 1", "busy", "confirmed")


def test_utc_event():
    ev = convert("BEGIN:VEVENT\nUID:u\nDTSTART:20261022T010000Z\nDTEND:20261022T020000Z\nEND:VEVENT\n")
    assert ev.tzid == "UTC" and ev.start.utcoffset() == timedelta(0)


def test_floating_event_has_no_zone():
    ev = convert("BEGIN:VEVENT\nUID:u\nDTSTART:20261023T090000\nDTEND:20261023T100000\nEND:VEVENT\n")
    assert ev.tzid == "floating" and ev.start.tzinfo is None and ev.end.tzinfo is None


def test_multi_day_all_day_keeps_exclusive_end():
    ev = convert("BEGIN:VEVENT\nUID:u\nDTSTART;VALUE=DATE:20261024\nDTEND;VALUE=DATE:20261027\nEND:VEVENT\n")
    assert ev.all_day and ev.tzid is None
    assert (ev.start, ev.end) == (date(2026, 10, 24), date(2026, 10, 27))


def test_all_day_without_dtend_lasts_one_day():
    ev = convert("BEGIN:VEVENT\nUID:u\nDTSTART;VALUE=DATE:20261024\nEND:VEVENT\n")
    assert ev.end == date(2026, 10, 25)


def test_duration_instead_of_dtend():
    ev = convert("BEGIN:VEVENT\nUID:u\nDTSTART;TZID=Asia/Tokyo:20261022T100000\nDURATION:PT90M\nEND:VEVENT\n")
    assert ev.end - ev.start == timedelta(minutes=90)


def test_non_iana_zone_is_converted_to_utc_keeping_the_instant():
    ics = (
        "BEGIN:VTIMEZONE\nTZID:Made Up Standard Time\nBEGIN:STANDARD\nDTSTART:19700101T000000\n"
        "TZOFFSETFROM:+0300\nTZOFFSETTO:+0300\nEND:STANDARD\nEND:VTIMEZONE\n"
        "BEGIN:VEVENT\nUID:u\nDTSTART;TZID=Made Up Standard Time:20261022T120000\n"
        "DTEND;TZID=Made Up Standard Time:20261022T130000\nEND:VEVENT\n"
    )
    ev = convert(ics)
    assert ev.tzid == "UTC"
    assert ev.start == datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def test_transparent_and_cancelled_and_alarms_and_attendees():
    ev = convert(
        "BEGIN:VEVENT\nUID:u\nDTSTART;TZID=Asia/Tokyo:20261022T100000\nDTEND;TZID=Asia/Tokyo:20261022T110000\n"
        "TRANSP:TRANSPARENT\nSTATUS:CANCELLED\nATTENDEE:mailto:a@example.com\n"
        "BEGIN:VALARM\nACTION:DISPLAY\nTRIGGER:-PT15M\nDESCRIPTION:x\nEND:VALARM\nEND:VEVENT\n"
    )
    assert (ev.availability, ev.status) == ("free", "cancelled")
    assert ev.alarms_minutes == (15,) and ev.attendees == ("a@example.com",)


def test_recurring_summary_is_display_only():
    ev = convert(
        "BEGIN:VEVENT\nUID:series\nDTSTART;TZID=Asia/Manila:20261020T150000\nDTEND;TZID=Asia/Manila:20261020T160000\n"
        "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261231T000000Z\nEXDATE;TZID=Asia/Manila:20261022T150000\nEND:VEVENT\n"
    )
    assert ev.is_recurring and ev.series_id == "series"
    assert ev.recurrence["frequency"] == "weekly" and ev.recurrence["by_weekday"] == ["TU", "TH"]
    assert "FREQ=WEEKLY" in ev.rrule_text and ev.exceptions_count == 1


def test_override_occurrence_is_recurring_with_its_recurrence_id():
    ev = convert(
        "BEGIN:VEVENT\nUID:series\nRECURRENCE-ID;TZID=Asia/Manila:20261027T150000\n"
        "DTSTART;TZID=Asia/Manila:20261027T170000\nDTEND;TZID=Asia/Manila:20261027T180000\nEND:VEVENT\n"
    )
    assert ev.is_recurring and ev.occurrence_start.hour == 15 and ev.start.hour == 17


def test_summarize_rrule_none():
    assert summarize_rrule(None) == (None, None)
