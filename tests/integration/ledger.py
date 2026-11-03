"""A durable cleanup ledger for real-account test events.

The planned event_id is written to disk BEFORE confirm_change runs, so a create that succeeds but whose
answer or read-back is lost is still tracked. Cleanup only ever touches ledger entries that are on the TEST
calendar and carry the test prefix, and every failure is reported, never swallowed.
"""

from __future__ import annotations

import json
from pathlib import Path

from icloud_mcp_server.core.errors import CalendarError


class CleanupFailed(AssertionError):
    pass


class Ledger:
    def __init__(self, path: Path, svc, calendar_id: str, prefix: str):
        self.path, self.svc, self.calendar_id, self.prefix = path, svc, calendar_id, prefix

    def _write(self, event_id: str, state: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_id": event_id, "state": state}) + "\n")

    def plan(self, event_id: str) -> None:
        self._write(event_id, "planned")

    def done(self, event_id: str) -> None:
        self._write(event_id, "gone")

    def outstanding(self) -> list[str]:
        state: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    state[row["event_id"]] = row["state"]
        return [eid for eid, st in state.items() if st != "gone"]

    def cleanup(self) -> list[str]:
        """Delete every outstanding test event. Returns descriptions of the ones that could not be removed."""
        leftovers: list[str] = []
        for eid in self.outstanding():
            try:
                self._remove(eid)
            except CalendarError as exc:
                leftovers.append(f"{eid}: {exc.code}")
            except CleanupFailed as exc:
                leftovers.append(f"{eid}: {exc}")
        return leftovers

    def _remove(self, eid: str) -> None:
        try:
            event = self.svc.get_event(eid)
        except CalendarError as exc:
            if exc.code == "NOT_FOUND":
                self.done(eid)
                return
            raise
        if event.calendar_id != self.calendar_id or not (event.untrusted.title or "").startswith(self.prefix):
            raise CleanupFailed("refused: not a test event on the TEST calendar")
        out = self.svc.preview_delete(eid)
        if out.needs_input:
            out = self.svc.preview_delete(eid, scope="whole_series")
        self.svc.confirm_change(out.confirm_token)
        try:
            self.svc.get_event(eid)
        except CalendarError as exc:
            if exc.code == "NOT_FOUND":
                self.done(eid)
                return
            raise
        raise CleanupFailed("still present after delete")

    def assert_clean(self) -> None:
        leftovers = self.cleanup()
        if leftovers:
            raise CleanupFailed("test events left on the real TEST calendar: " + "; ".join(leftovers))
