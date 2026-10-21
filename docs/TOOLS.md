# TOOLS: MCP tool set (Phase 2)

Status: spec for approval. Nothing is built. Written 2026-10-21.
**UNVERIFIED** = not confirmed against current official documentation.

Both backends (LOCAL EventKit, HOSTED CalDAV) implement exactly these tools. Preview tools and `confirm_change` exist only when the server is started with `--allow-writes`.

> **Mail tools** (`list_mailboxes`, `search_messages`, `get_message`, `get_thread`, `create_draft`) are specified in [MAIL.md](MAIL.md). They reuse `confirm_change` below; `create_draft` is an additional action type alongside create/update/delete of events. Scope is iCloud only.

## 0. Shared conventions

- **Times:** ISO 8601 with offset, plus an IANA `timezone`. Every event also has `display`: its time rendered in each configured display zone (default `Asia/Tokyo` and `Asia/Manila`).
- **All-day events:** `all_day: true`, `start_date` / `end_date` as `YYYY-MM-DD` (end exclusive), `timezone: null`. They are never converted between zones.
- **Floating times** (no zone in the source) are returned as `"timezone": "floating"` and interpreted in `Asia/Tokyo`. The output says so.
- **IDs:** `event_id` and `calendar_id` are opaque. Recurring occurrences add `occurrence_start` and `series_id`.
- **Untrusted text:** `title`, `notes`, `location`, `url` are returned under `untrusted` and described to the model as data, never instructions.
- **Limits:** range up to 366 days, up to 200 results per call, `cursor` for paging.
- **Annotations:** every tool has a short `title` and both hints set (`readOnlyHint`, `destructiveHint`). Read tools and preview tools: `readOnlyHint: true`, `destructiveHint: false`. `confirm_change`: `readOnlyHint: false`, `destructiveHint: true`. Per Anthropic's connector docs, read-only tools can run without a per-call prompt and destructive tools always prompt ([source](https://claude.com/docs/connectors/building/review-criteria); behaviour for custom connectors **UNVERIFIED**). Hints must match what the code does: preview tools write nothing to the calendar, only an in-memory pending record.
- **Read/write separation:** no tool mixes reading and writing; create, update and delete are separate tools.
- **Write gating:** `--allow-writes` plus a `writable_calendars` allow-list. Automated tests may only write to a calendar named `TEST`.

### Shared schema fragments (JSON Schema 2020-12)

```json
{
  "$defs": {
    "isoDateTime": {"type": "string", "format": "date-time"},
    "isoDate": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "tz": {"type": "string", "description": "IANA zone, e.g. Asia/Tokyo"},
    "display": {
      "type": "object",
      "additionalProperties": {"type": "string"},
      "description": "Zone name -> human string, e.g. {\"Asia/Tokyo\": \"Thu 22 Oct 11:00-12:00 JST\"}"
    },
    "recurrence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["frequency"],
      "properties": {
        "frequency": {"enum": ["daily", "weekly", "monthly", "yearly"]},
        "interval": {"type": "integer", "minimum": 1, "default": 1},
        "by_weekday": {"type": "array", "items": {"enum": ["MO","TU","WE","TH","FR","SA","SU"]}},
        "by_month_day": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 31}},
        "count": {"type": "integer", "minimum": 1},
        "until": {"$ref": "#/$defs/isoDate"}
      },
      "not": {"required": ["count", "until"]}
    },
    "eventSummary": {
      "type": "object",
      "required": ["event_id", "calendar_id", "calendar_name", "all_day", "is_recurring", "display", "untrusted"],
      "properties": {
        "event_id": {"type": "string"},
        "occurrence_start": {"$ref": "#/$defs/isoDateTime"},
        "series_id": {"type": "string"},
        "is_recurring": {"type": "boolean"},
        "calendar_id": {"type": "string"},
        "calendar_name": {"type": "string"},
        "all_day": {"type": "boolean"},
        "start": {"$ref": "#/$defs/isoDateTime"},
        "end": {"$ref": "#/$defs/isoDateTime"},
        "start_date": {"$ref": "#/$defs/isoDate"},
        "end_date": {"$ref": "#/$defs/isoDate"},
        "timezone": {"type": ["string", "null"]},
        "availability": {"enum": ["busy", "free", "tentative", "unavailable"]},
        "display": {"$ref": "#/$defs/display"},
        "untrusted": {
          "type": "object",
          "properties": {"title": {"type": "string"}, "location": {"type": "string"}}
        }
      }
    },
    "error": {
      "type": "object",
      "required": ["error", "message"],
      "properties": {
        "error": {"type": "string"},
        "message": {"type": "string"},
        "next_step": {"type": "string"}
      }
    }
  }
}
```

---

## 1. `list_calendars` (read)

Input: `{}`

Output: array of
`{calendar_id, calendar_name, source, color, writable_by_server, read_only_in_source, default_timezone}`

```json
{"calendar_id":"c1","calendar_name":"Work","source":"iCloud","color":"#1BADF8",
 "writable_by_server":false,"read_only_in_source":false,"default_timezone":"Asia/Tokyo"}
```

Input schema: `{"type":"object","additionalProperties":false,"properties":{}}`

## 2. `search_events` (read)

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["start", "end"],
  "properties": {
    "start": {"type": "string", "description": "date or date-time"},
    "end": {"type": "string"},
    "query": {"type": "string", "description": "case-insensitive match on title/notes/location"},
    "calendar_ids": {"type": "array", "items": {"type": "string"}},
    "include_all_day": {"type": "boolean", "default": true},
    "cursor": {"type": "string"}
  }
}
```

Output: `{"events": [eventSummary...], "next_cursor": "..."}`. Recurring series are expanded into occurrences by the platform library (EventKit for LOCAL, `recurring-ical-events` for HOSTED). No custom recurrence code.

Example: "What's on 22 Oct?"
```json
{"event_id":"e9","calendar_name":"Work","all_day":false,
 "start":"2026-10-22T10:00:00+08:00","end":"2026-10-22T11:00:00+08:00",
 "timezone":"Asia/Manila","is_recurring":false,
 "display":{"Asia/Tokyo":"Thu 22 Oct 11:00-12:00 JST","Asia/Manila":"Thu 22 Oct 10:00-11:00 PHT"},
 "untrusted":{"title":"Client sync"}}
```

## 3. `get_event` (read)

Input: `{event_id: string (required), occurrence_start?: date-time}`

Output: eventSummary plus `untrusted.notes`, `untrusted.url`, `recurrence` (structured form of the recurrence schema, or `null`), `rrule_text` (raw, display only), `exceptions_count`, `attendees` (read-only list), `alarms_minutes`, `last_modified`, `version` (opaque; used for conflict detection).

## 4. `find_free_time` (read)

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["start", "end", "duration_minutes"],
  "properties": {
    "start": {"type": "string"}, "end": {"type": "string"},
    "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 480},
    "working_hours": {"type": "object", "properties": {
      "start": {"type": "string", "pattern": "^\\d{2}:\\d{2}$"},
      "end": {"type": "string", "pattern": "^\\d{2}:\\d{2}$"}}, "default": {"start": "09:00", "end": "18:00"}},
    "working_hours_timezone": {"type": "string", "default": "Asia/Tokyo"},
    "weekdays": {"type": "array", "items": {"enum": ["MO","TU","WE","TH","FR","SA","SU"]}, "default": ["MO","TU","WE","TH","FR"]},
    "calendar_ids": {"type": "array", "items": {"type": "string"}},
    "count_all_day_as_busy": {"type": "boolean", "default": false},
    "ignore_free_availability": {"type": "boolean", "default": true},
    "overlap_timezone": {"type": "string"},
    "overlap_working_hours": {"type": "object"},
    "max_results": {"type": "integer", "default": 10, "maximum": 50}
  }
}
```

Logic is interval subtraction over already-expanded busy occurrences. Output: `[{start, end, display}]`.

Example: 60 minutes, 23-24 Oct, Tokyo 09:00-18:00:
```json
{"start":"2026-10-23T13:00:00+09:00","end":"2026-10-23T14:00:00+09:00",
 "display":{"Asia/Tokyo":"Fri 13:00-14:00 JST","Asia/Manila":"Fri 12:00-13:00 PHT"}}
```

With `overlap_timezone: "Asia/Manila"` plus `overlap_working_hours`, only slots inside both working windows are returned.

## 5. `create_event` (preview only; writes nothing)

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["calendar_id", "title"],
  "properties": {
    "calendar_id": {"type": "string"},
    "title": {"type": "string", "maxLength": 500},
    "start": {"$ref": "#/$defs/isoDateTime"}, "end": {"$ref": "#/$defs/isoDateTime"},
    "timezone": {"$ref": "#/$defs/tz"},
    "all_day": {"type": "boolean", "default": false},
    "start_date": {"$ref": "#/$defs/isoDate"}, "end_date": {"$ref": "#/$defs/isoDate"},
    "location": {"type": "string"}, "notes": {"type": "string"},
    "alarms_minutes": {"type": "array", "items": {"type": "integer", "minimum": 0}},
    "recurrence": {"$ref": "#/$defs/recurrence"}
  }
}
```

Rules: either (`start`, `end`, `timezone`) or (`all_day: true`, `start_date`, `end_date`). Recurrence is structured only; the library emits the RRULE. Attendees are not supported in v1 because inviting people sends real emails.

Output:
```json
{"preview":{"action":"create","calendar":"TEST","fields":{"title":"Dentist","start":"2026-11-02T15:00:00+09:00","end":"2026-11-02T16:00:00+09:00","timezone":"Asia/Tokyo"},
 "display":{"Asia/Tokyo":"Mon 2 Nov 15:00-16:00 JST","Asia/Manila":"Mon 2 Nov 14:00-15:00 PHT"},
 "recurrence_summary":null},
 "confirm_token":"<128-bit random>","expires_at":"2026-10-21T10:05:00+09:00"}
```

## 6. `update_event` (preview only)

Input: `event_id` (required), `occurrence_start?`, `scope?` (`this_occurrence` | `whole_series`), `changes` (object with any of: `title, start, end, timezone, location, notes, alarms_minutes, recurrence`).

- Recurring event and no `scope`: returns, and issues no token:
  ```json
  {"needs_input":"AMBIGUOUS_SCOPE","question":"Change only Thu 22 Oct, or every occurrence of 'Client sync'?"}
  ```
- `whole_series` with a time change shifts the series by the same delta; the preview warns if already-edited occurrences exist.
- `recurrence` changes are allowed only with `whole_series`.
- Moving between calendars is not supported (clear error).
- "This and future" is not offered in v1.

Output: `{"preview":{"action":"update","scope":...,"before":{...},"after":{...},"changed_fields":[...]},"confirm_token":...,"expires_at":...}`

## 7. `delete_event` (preview only)

Input: `event_id` (required), `occurrence_start?`, `scope?` (same rule as update). Exactly one event or one occurrence. No arrays, no query-based delete.

Output: `{"preview":{"action":"delete","scope":...,"event":{...},"warning":"This deletes all 26 remaining occurrences"},"confirm_token":...,"expires_at":...}`

## 8. `confirm_change` (the only write)

Input: `{"confirm_token": string}`

Token: 128-bit random, single-use, 5-minute TTL; server-side record holds the exact change and the target's `version` at preview time. Both modes run a single instance, so in-memory storage is enough in v1 (a restart invalidates pending tokens, which is the safe direction).

Steps:
1. Re-read the target. If `version` differs from preview time: `CONFLICT`, nothing written.
2. Apply the change.
3. Re-read and compare with the preview. Mismatch: `WRITE_VERIFICATION_FAILED`, with the actual state (guards against the EventKit occurrence-delete bug noted in DESIGN.md §3.4).

Output: `{"result":"applied","event":{...after state...}}`

Limits: 10 confirmations per 10 minutes; at most one delete per token.

## 9. Errors

| Code | When | Next step in the message |
|---|---|---|
| `PERMISSION_DENIED` | macOS calendar access missing | System Settings -> Privacy & Security -> Calendars |
| `ICLOUD_UNREACHABLE` | CalDAV network error, 5xx or 10 s timeout | Retry; check Apple system status |
| `CREDENTIALS_REJECTED` | CalDAV 401 | Password revoked or expired: regenerate at account.apple.com, update the secret |
| `CALENDAR_NOT_WRITABLE` | calendar not in allow-list, or read-only at source | Edit `writable_calendars` |
| `WRITES_DISABLED` | started without `--allow-writes` | Restart with the flag |
| `AMBIGUOUS_SCOPE` | recurring event, no scope | Ask the user |
| `TOKEN_INVALID` / `TOKEN_EXPIRED` | bad, used or old token | Re-run the preview |
| `CONFLICT` | event changed since preview | Re-run the preview |
| `WRITE_VERIFICATION_FAILED` | post-write state differs from preview | Shows actual state |
| `INVALID_TIMEZONE` / `INVALID_RANGE` | bad input | Fix the input |

## 10. Test fixtures required by this spec

- All-day event viewed from Manila (date unchanged).
- Tokyo event displayed in Manila (minus 1 hour).
- Weekly series created in `Asia/Manila`, viewed from Tokyo.
- Floating-time event.
- Series with one edited and one deleted occurrence.
- Every example above as a golden test.
- Preview -> confirm -> verify -> delete, against the `TEST` calendar only.
