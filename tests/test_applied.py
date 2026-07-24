"""Tests for the applied-tracking store and checker matching.

The point of this feature is to stop you re-applying, so the matching must be
robust to the same URL differences the digest already handles.
"""

from src.applied import AppliedStore
from src.check import _keys_for_url


class TestAppliedStore:
    def test_mark_then_status(self, tmp_path):
        store = AppliedStore(tmp_path / "applied.json")
        keys = ["gh:stripe:111"]
        assert store.status(keys) is None
        store.mark(keys, url="https://x/1", company="Stripe", title="SWE Intern")
        assert store.status(keys)["company"] == "Stripe"

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "applied.json"
        s = AppliedStore(path)
        s.mark(["gh:stripe:111"], url="https://x/1", company="Stripe", title="SWE")
        s.save()
        assert AppliedStore(path).status(["gh:stripe:111"])["company"] == "Stripe"

    def test_mark_is_idempotent_keeps_first_date(self, tmp_path):
        s = AppliedStore(tmp_path / "applied.json")
        first = s.mark(["gh:s:1"], url="u")["applied_at"]
        again = s.mark(["gh:s:1"], url="u")["applied_at"]
        assert first == again

    def test_unmark(self, tmp_path):
        s = AppliedStore(tmp_path / "applied.json")
        s.mark(["gh:s:1"], url="u")
        assert s.unmark(["gh:s:1"]) is True
        assert s.status(["gh:s:1"]) is None

    def test_all_records_dedupes_shared_record(self, tmp_path):
        s = AppliedStore(tmp_path / "applied.json")
        # One job, two keys (url + identity) -> one record, not two.
        s.mark(["gh:s:1", "id:stripe|swe|nyc"], url="u", company="Stripe", title="SWE")
        assert len(s.all_records()) == 1

    def test_all_records_dedupes_after_save_reload(self, tmp_path):
        """After a reload the two keys hold separate equal dicts; still one job."""
        path = tmp_path / "applied.json"
        s = AppliedStore(path)
        s.mark(["gh:s:1", "id:stripe|swe|nyc"], url="u", company="Stripe", title="SWE")
        s.save()
        assert len(AppliedStore(path).all_records()) == 1

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "applied.json"
        path.write_text("{not json")
        assert AppliedStore(path).all_records() == []


class TestCheckerMatching:
    def test_recognises_applied_job_under_a_different_url(self, tmp_path):
        """Applied via a tracking-tagged URL, checked via a clean one -> match."""
        store = AppliedStore(tmp_path / "applied.json")
        applied_url = "https://job-boards.greenhouse.io/appian/jobs/8041237?gh_src=Simplify"
        checked_url = "https://boards.greenhouse.io/appian/jobs/8041237"
        store.mark(_keys_for_url(applied_url), url=applied_url)
        assert store.status(_keys_for_url(checked_url)) is not None

    def test_recognises_workday_across_host_shards(self, tmp_path):
        store = AppliedStore(tmp_path / "applied.json")
        u1 = "https://nvidia.wd5.myworkdayjobs.com/en-US/site/job/US-CA/Intern_JR2015779"
        u2 = "https://nvidia.wd1.myworkdayjobs.com/site/job/Remote/Intern_JR2015779"
        store.mark(_keys_for_url(u1), url=u1)
        assert store.status(_keys_for_url(u2)) is not None

    def test_different_jobs_do_not_match(self, tmp_path):
        store = AppliedStore(tmp_path / "applied.json")
        store.mark(_keys_for_url("https://boards.greenhouse.io/appian/jobs/8041237"), url="x")
        other = _keys_for_url("https://boards.greenhouse.io/appian/jobs/9999999")
        assert store.status(other) is None
