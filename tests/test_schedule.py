"""Tests for the delay-tolerant send scheduling.

Two things are being protected here.

1. A slot sends whenever it is "due and unsent", not at an exact minute, so a
   delayed or dropped GitHub Actions cron trigger can't skip a whole slot (the
   bug that once dropped a 7pm email entirely).

2. The email nonetheless LANDS on the hour. The window opens `prep_minutes`
   early so the run can scrape and render, then holds the finished email until
   the target time.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.main import (_check_stale, due_slot, hold_until_send_time,
                      mark_slot_sent, send_targets, window_slot)

CFG = {
    "send_hours": [7, 19],
    "timezone": "America/New_York",
    "schedule": {
        "prep_minutes": 25,
        "max_hold_minutes": 45,
        "am_deadline": "12:00",
        "pm_deadline": "23:30",
    },
}
TZ = ZoneInfo("America/New_York")
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
        ("7am, AM unsent", at(7), FakeStore(), "AM"),
        # Prep window: a 6:40 trigger claims the 7am slot and waits for it.
        ("6:40am prep window", at(6, 40), FakeStore(), "AM"),
        ("6:20am, before the prep window opens", at(6, 20), FakeStore(), None),
        ("11am delayed, AM unsent", at(11), FakeStore(), "AM"),
        ("11am, AM already sent", at(11), FakeStore(am=TODAY), None),
        ("2am, before morning window", at(2), FakeStore(), None),
        # Past the AM deadline the morning is abandoned rather than delivered
        # at teatime; the evening digest sweeps up its jobs.
        ("1pm, past the AM deadline", at(13), FakeStore(), None),
        ("11:59pm, past the PM deadline", at(23, 59), FakeStore(), None),
        ("evening covers a missed morning", at(19), FakeStore(), "PM"),
    ],
)
def test_due_slot(desc, now, store, expected):
    assert due_slot(CFG, now, store) == expected, desc


def test_marking_prevents_resend_in_same_window():
    store = FakeStore()
    now = at(20)
    slot = due_slot(CFG, now, store)
    assert slot == "PM"
    mark_slot_sent(store, slot, now)
    assert due_slot(CFG, at(20, 15), store) is None


def test_window_slot_boundaries():
    assert window_slot(CFG, at(6, 34)) is None      # prep window not yet open
    assert window_slot(CFG, at(6, 35)) == "AM"      # 25 min before 07:00
    assert window_slot(CFG, at(7)) == "AM"
    assert window_slot(CFG, at(12)) == "AM"         # deadline is inclusive
    assert window_slot(CFG, at(12, 1)) is None      # AM abandoned
    assert window_slot(CFG, at(18, 35)) == "PM"
    assert window_slot(CFG, at(19)) == "PM"
    assert window_slot(CFG, at(23, 30)) == "PM"
    assert window_slot(CFG, at(23, 31)) is None


class TestMarkSlotSent:
    """The slot recorded must be the one the run DECIDED on, not one re-derived
    from the clock -- a run that starts at 06:40 and sends at 07:00 is AM."""

    def test_am_recorded_for_a_prep_window_run(self):
        store = FakeStore()
        mark_slot_sent(store, "AM", at(6, 40))
        assert store.last_am_sent == TODAY
        assert store.last_pm_sent == ""

    def test_delayed_am_run_is_still_am(self):
        store = FakeStore()
        # 11:30am is past noon-ish heuristics but is unambiguously the AM slot.
        mark_slot_sent(store, "AM", at(11, 30))
        assert store.last_am_sent == TODAY
        assert store.last_pm_sent == ""

    def test_forced_run_outside_a_window_marks_nothing(self):
        store = FakeStore()
        mark_slot_sent(store, None, at(3))
        assert store.last_am_sent == ""
        assert store.last_pm_sent == ""


class TestHold:
    """The wait that makes the email land on the hour."""

    def _slept(self, now, slot, cfg=CFG, **kw):
        calls = []
        # hold_until_send_time reads the real clock to measure the remaining
        # delay, so freeze it by handing it the moment under test.
        import src.main as main
        real = main.datetime

        class Frozen(real):
            @classmethod
            def now(cls, tz=None):
                return now

        main.datetime = Frozen
        try:
            return hold_until_send_time(cfg, now, slot, sleep=calls.append, **kw), calls
        finally:
            main.datetime = real

    def test_waits_until_the_target(self):
        delay, calls = self._slept(at(6, 40), "AM")
        assert delay == 20 * 60          # 06:40 -> 07:00
        assert calls == [20 * 60]

    def test_no_wait_when_the_target_has_passed(self):
        delay, calls = self._slept(at(7, 30), "AM")
        assert delay == 0
        assert calls == []

    def test_wait_is_capped(self):
        # An hour early is over the 45-minute cap: send rather than idle.
        delay, calls = self._slept(at(5, 55), "AM")
        assert delay == 0
        assert calls == []

    def test_disabled_by_no_hold(self):
        delay, calls = self._slept(at(6, 40), "AM", enabled=False)
        assert delay == 0
        assert calls == []

    def test_no_slot_means_no_wait(self):
        delay, calls = self._slept(at(6, 40), None)
        assert delay == 0
        assert calls == []


def test_send_targets_follow_config():
    targets = send_targets(CFG, at(6, 40))
    assert targets["AM"] == at(7, 0)
    assert targets["PM"] == at(19, 0)


class TestOnlyScheduledRunsWait:
    """Someone who clicked "Run workflow" wants the email now. Only the cron
    path holds for the target minute."""

    def test_forced_runs_do_not_hold(self):
        calls = []
        assert hold_until_send_time(CFG, at(6, 40), "AM",
                                    enabled=False, sleep=calls.append) == 0
        assert calls == []


# ---------------------------------------------------------------------------
# late_send: a missed window must degrade to a LATE email, never to silence.
#
# Regression cover for the 2026-08-26 outage. GitHub delivered 2 of 32
# scheduled triggers a day, hours off-target (16:17, 02:04 and 17:10 local),
# so not one run landed in a send window. Every run exited 0 with "Nothing
# due", every check was green, and no digest arrived for three days.
#
# CFG above deliberately omits `late_send`, so the cases higher up in this file
# still assert the old skip-past-the-deadline behaviour.
# ---------------------------------------------------------------------------

LATE_CFG = {**CFG, "schedule": {**CFG["schedule"], "late_send": True}}


@pytest.mark.parametrize(
    "desc,now,store,expected",
    [
        # The real Aug 27 / Aug 28 wake-ups. Both must now deliver.
        ("4:17pm, AM window missed entirely", at(16, 17), FakeStore(), "AM"),
        ("5:10pm, AM window missed entirely", at(17, 10), FakeStore(), "AM"),
        # 2:04am was the third surviving trigger; nothing is owed that early.
        ("2:04am, no slot has come due yet", at(2, 4), FakeStore(), None),
        ("1pm, past the AM deadline, AM unsent", at(13), FakeStore(), "AM"),
        ("11:59pm, past the PM deadline, PM unsent", at(23, 59), FakeStore(), "PM"),
        # Only the LATEST passed slot is offered, so a normal PM send is not
        # chased by a redundant "AM (late)" minutes later.
        ("11:59pm, PM sent, AM never sent", at(23, 59), FakeStore(pm=TODAY), None),
        ("5pm, AM already sent normally", at(17), FakeStore(am=TODAY), None),
        # Inside a window, nothing changes -- this is not the late path.
        ("7pm exact still routes through the window", at(19), FakeStore(), "PM"),
    ],
)
def test_late_send_covers_a_missed_window(desc, now, store, expected):
    assert due_slot(LATE_CFG, now, store) == expected, desc


@pytest.mark.parametrize("now", [at(13), at(16, 17), at(23, 59)])
def test_late_send_off_keeps_skipping(now):
    """The escape hatch still works: late_send: false is the old behaviour."""
    assert due_slot(CFG, now, FakeStore()) is None


def test_a_late_slot_is_marked_sent_like_any_other():
    """A late digest must still record itself, or the next run resends it."""
    store = FakeStore()
    slot = due_slot(LATE_CFG, at(16, 17), store)
    mark_slot_sent(store, slot, at(16, 17))
    assert store.last_am_sent == TODAY
    assert due_slot(LATE_CFG, at(17, 30), store) is None


def test_late_send_does_not_hold():
    """The target is already past, so a late run sends immediately."""
    assert hold_until_send_time(LATE_CFG, at(16, 17), "AM",
                                sleep=lambda s: pytest.fail("must not sleep")) == 0.0


# ---------------------------------------------------------------------------
# The watchdog. Silence was the one failure this project could not see.
# ---------------------------------------------------------------------------

class TestCheckStale:
    def test_today_is_fine(self):
        assert _check_stale(FakeStore(am=TODAY, pm=TODAY), at(20)) == 0

    def test_yesterday_is_fine(self):
        assert _check_stale(FakeStore(pm="2026-07-24"), at(20)) == 0

    def test_three_days_of_silence_goes_red(self):
        """The actual outage: last send 08-26, checked on 08-29."""
        store = FakeStore(am="2026-08-26", pm="2026-08-26")
        assert _check_stale(store, datetime(2026, 8, 29, 21, 30, tzinfo=TZ)) == 1

    def test_never_sent_goes_red(self):
        assert _check_stale(FakeStore(), at(20)) == 1

    def test_the_more_recent_slot_wins(self):
        """AM stale but PM fresh is a healthy digest, not a stale one."""
        assert _check_stale(FakeStore(am="2026-01-01", pm=TODAY), at(20)) == 0

    def test_threshold_is_configurable(self):
        store = FakeStore(pm="2026-07-22")
        assert _check_stale(store, at(20), max_age_days=1) == 1
        assert _check_stale(store, at(20), max_age_days=5) == 0
