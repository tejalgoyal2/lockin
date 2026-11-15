"""When a scheduled run should scan (the `gate` job of .github/workflows/scan.yml).

GitHub starts a scheduled workflow hours after its cron time on this repo (2.5 to 6.5 h, measured Nov 2026), so a cron
set to the wanted hour lands in the afternoon. Instead the workflow is triggered every two hours and this module turns
each trigger into "wait until the next slot, then scan" or "nothing to do". The first trigger to reach a slot runs it;
state/last_slot remembers the slot, so the triggers behind it stop at once.

Slots are 08:00 and 16:00 in British Columbia, which stays on UTC-7 all year since March 2026: 15:00 and 23:00 UTC.
Standard library only: the gate job runs this before any package is installed.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SLOT_HOURS_UTC = (15, 23)
GRACE = timedelta(hours=4)             # a trigger arriving this late after a slot nobody ran still runs it
MAX_WAIT = timedelta(hours=5, minutes=30)   # a job may run 6 h; a trigger further from its slot leaves it to the next one
STATE = Path("state/last_slot")


def slot_id(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%MZ")


def plan(now: datetime, last_done: str) -> tuple[str, float]:
    """(slot to run, seconds to wait first), or ("", 0) when this trigger has nothing to do."""
    day = now.replace(minute=0, second=0, microsecond=0)
    slots = sorted(day.replace(hour=h) + timedelta(days=d) for d in (-1, 0, 1) for h in SLOT_HOURS_UTC)
    for slot in slots:
        if slot < now - GRACE or slot_id(slot) <= last_done:
            continue
        wait = slot - now
        if wait > MAX_WAIT:
            return "", 0.0
        return slot_id(slot), max(0.0, wait.total_seconds())
    return "", 0.0


def main() -> int:
    last = STATE.read_text(encoding="utf-8").strip() if STATE.is_file() else ""
    slot, wait = plan(datetime.now(timezone.utc), last)
    print(f"slot={slot}")
    print(f"wait={int(wait)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
