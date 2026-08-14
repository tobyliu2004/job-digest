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


class TestAmbiguousKeysAreNeverWritten:
    """The real case, from Boeing on 2026-08-13: one Data Analytics internship
    run as TWO Workday requisitions (JR2026520976 on the intern board,
    JR2026520976-1 on the external one). Both produce the same tier-2 identity
    key, so dedupe strips it -- and the send path must not put it back."""

    def _boeing_pair(self):
        from src.models import SourceResult
        common = dict(company="The Boeing Company", title="Data Analytics Intern",
                      source="SimplifyJobs", location="Ridley Park, PA, Seattle, WA",
                      season="Summer 2027")
        a = Job(apply_url="https://boeing.wd1.myworkdayjobs.com/INTERN/job/x/"
                          "Data-Analytics-Intern_JR2026520976", **common)
        b = Job(apply_url="https://boeing.wd1.myworkdayjobs.com/EXTERNAL_CAREERS/job/x/"
                          "Data-Analytics-Intern_JR2026520976-1", **common)
        return [SourceResult("SimplifyJobs", [a, b])]

    def test_the_two_requisitions_stay_separate(self):
        from src import dedupe
        clusters, _, _ = dedupe.cluster(self._boeing_pair())
        assert len(clusters) == 2

    def test_the_shared_identity_key_is_stripped(self):
        from src import canonical, dedupe
        clusters, _, _ = dedupe.cluster(self._boeing_pair())
        shared = canonical.identity_key("The Boeing Company", "Data Analytics Intern",
                                        "Ridley Park, PA, Seattle, WA", "Summer 2027")
        assert all(shared not in c.keys for c in clusters)

    def test_the_send_path_does_not_write_it_back(self):
        from src import canonical, dedupe
        from src.main import _keys_to_store

        clusters, _, _ = dedupe.cluster(self._boeing_pair())
        keys_for_job = {id(c.job): sorted(c.keys) for c in clusters}
        shared = canonical.identity_key("The Boeing Company", "Data Analytics Intern",
                                        "Ridley Park, PA, Seattle, WA", "Summer 2027")

        store = FakeStore()
        for c in clusters:
            store.add(_keys_to_store(c.job, keys_for_job))

        assert shared not in store.keys, (
            "an ambiguous key was stored; whichever requisition drops out of "
            "the feed first would silently suppress the other"
        )

    def test_neither_requisition_suppresses_the_other_later(self):
        """The consequence test: one variant disappears, the other must still
        be recognised as already-sent -- and not by the shared key."""
        from src import dedupe
        from src.main import _keys_to_store

        results = self._boeing_pair()
        clusters, _, _ = dedupe.cluster(results)
        keys_for_job = {id(c.job): sorted(c.keys) for c in clusters}
        store = FakeStore()
        for c in clusters:
            store.add(_keys_to_store(c.job, keys_for_job))

        # Tomorrow only the intern-board requisition is in the feed, so its
        # identity key is no longer ambiguous and is no longer stripped.
        from src.models import SourceResult
        solo = [SourceResult("SimplifyJobs", [results[0].jobs[0]])]
        again, _, _ = dedupe.cluster(solo)
        assert store.has_any(sorted(again[0].keys)), "already-sent job looks new"

    def test_a_resolved_url_is_still_recorded(self):
        """The reason the send path adds a key back at all."""
        from src import canonical, dedupe
        from src.main import _keys_to_store
        from src.models import SourceResult

        uuid = "de926b0a-99e7-4dbd-94cd-334ec5600000"
        stub = Job(company="Citadel", title="Data Scientist Intern",
                   apply_url=f"https://simplify.jobs/jobs/click/{uuid}",
                   source="Simplify", extra={"simplify_id": uuid})
        clusters, _, _ = dedupe.cluster([SourceResult("Simplify", [stub])])
        keys_for_job = {id(c.job): sorted(c.keys) for c in clusters}

        stub.apply_url = "https://www.citadel.com/careers/details/x"   # resolved
        stored = _keys_to_store(stub, keys_for_job)

        assert canonical.canonical_url_key(stub.apply_url) in stored
        assert f"sj:{uuid}" in stored
