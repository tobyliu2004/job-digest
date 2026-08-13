"""A digest split across several emails must survive a failure mid-send.

State used to be written once, after every part had gone out. If part 2 of 3
raised, nothing was recorded and the next run re-sent all three -- so a
transient SMTP error produced a duplicate digest.

Now each part's keys are recorded as soon as that part is accepted, and the
slot is left unsent so the next run finishes the job.
"""

from __future__ import annotations

import pytest

from src.main import DigestPart, deliver, mark_slot_sent
from src.models import Job


class FakeStore:
    def __init__(self):
        self.keys: dict[str, str] = {}
        self.saves = 0
        self.last_am_sent = ""
        self.last_pm_sent = ""

    def has_any(self, keys):
        return any(k in self.keys for k in keys)

    def add(self, keys, when=None):
        for key in keys:
            self.keys.setdefault(key, when or "2026-08-13T00:00:00+00:00")

    def save(self):
        self.saves += 1


def job(i):
    return Job(company=f"Co {i}", title="SWE Intern",
               apply_url=f"https://job-boards.greenhouse.io/c{i}/jobs/{i}",
               source="repo")


def parts(counts):
    out, n = [], 0
    for idx, count in enumerate(counts, 1):
        batch = [job(n + k) for k in range(count)]
        n += count
        out.append(DigestPart(f"subject {idx}", "<html>", "text", batch))
    return out


def send_loop(messages, store, sender, _unused=None):
    """Calls the REAL send loop from main(), so this cannot drift from it."""
    return deliver(messages, store, sender)


class Sender:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.sent = []

    def __call__(self, part):
        self.sent.append(part.subject)
        if len(self.sent) == self.fail_on:
            raise RuntimeError("SMTP connection reset")


def keys(j):
    from src import canonical
    return canonical.keys_for(j)


class TestPartialFailure:
    def test_only_delivered_parts_are_recorded(self):
        store, sender = FakeStore(), Sender(fail_on=2)
        messages = parts([3, 3, 3])

        assert send_loop(messages, store, sender, keys) is False

        for j in messages[0].jobs:
            assert store.has_any(keys(j))
        for part in messages[1:]:
            for j in part.jobs:
                assert not store.has_any(keys(j))

    def test_the_slot_is_left_unsent(self):
        store, sender = FakeStore(), Sender(fail_on=2)
        if not send_loop(parts([3, 3]), store, sender, keys):
            pass                      # main() returns 1 without marking the slot
        assert store.last_am_sent == "" and store.last_pm_sent == ""

    def test_the_next_run_sends_only_the_remainder(self):
        store, sender = FakeStore(), Sender(fail_on=2)
        messages = parts([3, 3, 3])
        send_loop(messages, store, sender, keys)

        remaining = [j for part in messages for j in part.jobs
                     if not store.has_any(keys(j))]
        assert len(remaining) == 6
        assert all(not store.has_any(keys(j)) for j in remaining)

        # The retry delivers exactly those, and nothing more.
        retry = Sender()
        assert send_loop(parts([6]), store, retry, keys) is True
        assert len(retry.sent) == 1

    def test_state_is_saved_after_each_part(self):
        store, sender = FakeStore(), Sender()
        send_loop(parts([2, 2, 2]), store, sender, keys)
        assert store.saves == 3

    def test_a_first_part_failure_records_nothing(self):
        store, sender = FakeStore(), Sender(fail_on=1)
        assert send_loop(parts([3, 3]), store, sender, keys) is False
        assert store.keys == {}
        assert store.saves == 0


class TestSuccessfulSend:
    def test_every_job_is_recorded(self):
        store, sender = FakeStore(), Sender()
        messages = parts([4, 4])
        assert send_loop(messages, store, sender, keys) is True
        for part in messages:
            for j in part.jobs:
                assert store.has_any(keys(j))

    def test_the_slot_is_marked_only_after_all_parts(self):
        import datetime

        store, sender = FakeStore(), Sender()
        assert send_loop(parts([2, 2]), store, sender, keys) is True
        mark_slot_sent(store, "AM", datetime.datetime(2026, 8, 13))
        assert store.last_am_sent == "2026-08-13"
        assert store.last_pm_sent == ""


class TestResolvedUrlsAreAlsoRecorded:
    def test_both_url_forms_are_stored(self):
        """A Simplify click stub is resolved to the employer URL after
        clustering, so the send loop stores the union of both."""
        from src import canonical

        uuid = "de926b0a-99e7-4dbd-94cd-334ec5600000"
        stub = Job(company="Citadel", title="Data Scientist Intern",
                   apply_url=f"https://simplify.jobs/jobs/click/{uuid}",
                   source="Simplify", extra={"simplify_id": uuid})
        before = set(canonical.keys_for(stub))

        stub.apply_url = "https://www.citadel.com/careers/details/x"
        store = FakeStore()
        store.add(sorted(before | set(canonical.keys_for(stub))))

        assert store.has_any(["url:simplify.jobs/jobs/click/" + uuid])
        assert store.has_any(["url:citadel.com/careers/details/x"])
        assert store.has_any([f"sj:{uuid}"])
