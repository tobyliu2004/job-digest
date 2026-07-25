"""Tests for the delay-tolerant send scheduling.

The digest sends whenever a slot is "due and unsent", not at an exact minute,
so a delayed or dropped GitHub Actions cron trigger can't skip a whole slot
(the bug that once dropped a 7pm email entirely).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.main import due_slot, mark_slot_sent, window_slot

CFG = {"send_hours": [9, 19], "timezone": "America/Los_Angeles"}
TZ = ZoneInfo("America/Los_Angeles")
TODAY = "2026-07-25"


def at(hour, minute=0):
    return datetime(2026, 7, 25, hour, minute, tzinfo=TZ)


class FakeStore:
    def __init__(self, am="", pm=""):
        self.last_am_sent = am
        self.last_pm_sent = pm


@pytest.mark.parametrize(
    "desc,now,store,expected",
    [
        ("7pm exact, PM unsent", at(19), FakeStore(), "PM"),
        # The exact failure that dropped a 7pm email: a delayed run must still send.
        ("8pm delayed, PM unsent", at(20), FakeStore(), "PM"),
        ("11pm, PM unsent", at(23), FakeStore(), "PM"),
        ("8:30pm, PM already sent -> no double send", at(20, 30), FakeStore(pm=TODAY), None),
        ("9am, AM unsent", at(9), FakeStore(), "AM"),
        ("11am delayed, AM unsent", at(11), FakeStore(), "AM"),
        ("11am, AM already sent", at(11), FakeStore(am=TODAY), None),
        ("2am, before morning window", at(2), FakeStore(), None),
        ("evening covers a missed morning", at(19), FakeStore(), "PM"),
    ],
)
def test_due_slot(desc, now, store, expected):
    assert due_slot(CFG, now, store) == expected, desc


def test_marking_prevents_resend_in_same_window():
    store = FakeStore()
    now = at(20)
    assert due_slot(CFG, now, store) == "PM"
    mark_slot_sent(store, now, CFG)
    assert due_slot(CFG, at(20, 15), store) is None


def test_window_slot_boundaries():
    assert window_slot(CFG, at(8, 59)) is None
    assert window_slot(CFG, at(9)) == "AM"
    assert window_slot(CFG, at(18, 59)) == "AM"
    assert window_slot(CFG, at(19)) == "PM"
    assert window_slot(CFG, at(23, 59)) == "PM"
