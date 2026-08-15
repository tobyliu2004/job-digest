"""The key-format migration must not flood the inbox.

Changing a dedup key format invalidates every key already in state/seen.json,
so without a translation step the next digest treats the whole backlog as new
and emails hundreds of jobs already applied to. src/migrate.py translates the
old keys onto the new ones; these tests bound what that costs.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src import canonical, canonical_legacy, dedupe, migrate
from src.models import Job, SourceResult
from src.store import RETENTION_DAYS, SeenStore


def job(company="Acme", title="Software Engineer Intern", url="", source="repo",
        location="New York, NY", season="Summer 2027", **extra):
    return Job(company=company, title=title, apply_url=url, source=source,
               location=location, season=season, extra=extra or {})


class TestNoMassResend:
    """The whole point. Run against real production state (3,431 keys) and a
    real 1,407-job scrape."""

    def test_resend_is_small(self, corpus_results, seen_snapshot):
        report = migrate.run(seen_snapshot, corpus_results)
        assert report.one_time_resends == report.jobs_new_after - report.jobs_new_before
        # Without the migration this would be in the hundreds.
        assert report.one_time_resends < 60, report.render()

    def test_resend_is_a_tiny_fraction_of_the_corpus(self, corpus_results, seen_snapshot):
        clusters, _, _ = dedupe.cluster(corpus_results)
        report = migrate.run(seen_snapshot, corpus_results)
        assert report.one_time_resends < 0.10 * len(clusters), report.render()

    def test_without_migration_the_backlog_would_flood(self, corpus_results, seen_snapshot):
        """Shows what the migration is preventing: judging the same corpus with
        new keys against un-translated state marks far more of it new."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        untranslated = sum(1 for c in clusters if not seen_snapshot.has_any(sorted(c.keys)))
        report = migrate.run(seen_snapshot, corpus_results)
        assert report.jobs_new_after < 0.5 * untranslated, (
            f"{untranslated} would be sent raw vs {report.jobs_new_after} after migration"
        )

    def test_is_idempotent(self, corpus_results, seen_snapshot):
        """Running it twice must write nothing the second time. (The resend
        figure is a property of corpus-vs-state, so it does not change.)"""
        migrate.run(seen_snapshot, corpus_results)
        after_first = dict(seen_snapshot.keys)
        second = migrate.run(seen_snapshot, corpus_results)
        assert seen_snapshot.keys == after_first
        assert second.new_keys_written == 0

    def test_the_next_run_sends_nothing_extra(self, corpus_results, seen_snapshot):
        """After migrating, re-clustering the same scrape must yield only the
        one-time catch-up -- and after storing those, nothing at all."""
        report = migrate.run(seen_snapshot, corpus_results)
        clusters, _, _ = dedupe.cluster(corpus_results)
        new = [c for c in clusters if not seen_snapshot.has_any(sorted(c.keys))]
        assert len(new) == report.jobs_new_after

        for group in new:
            seen_snapshot.add(sorted(group.keys))
        again, _, _ = dedupe.cluster(corpus_results)
        assert [c for c in again if not seen_snapshot.has_any(sorted(c.keys))] == []


class TestPoisonedKeys:
    """A legacy key that matches several live postings was hiding jobs. It must
    not be translated, or the old bug becomes permanent."""

    def _poisoned_setup(self, tmp_path):
        # Three jobs the old URL key collapsed into one, because it discarded
        # the query string that identified them.
        jobs = [job(title=f"Software Engineer Intern {i}",
                    url=f"https://careers.acme.com/jobdetails?jobId={i}")
                for i in (1, 2, 3)]
        legacy = canonical_legacy.canonical_url_key(jobs[0].apply_url)
        assert all(canonical_legacy.canonical_url_key(j.apply_url) == legacy for j in jobs)

        path = tmp_path / "seen.json"
        path.write_text(json.dumps({"keys": {legacy: "2026-07-24T00:00:00+00:00"}}))
        return SeenStore(path), [SourceResult("repo", jobs)], legacy

    def test_poisoned_key_is_refused(self, tmp_path):
        store, results, _ = self._poisoned_setup(tmp_path)
        report = migrate.run(store, results)
        assert report.poisoned_keys == 1
        assert report.poisoned_jobs == 3
        assert report.legacy_keys_translated == 0

    def test_poisoned_jobs_are_all_treated_as_new(self, tmp_path):
        """At most one of the three was ever emailed, so sending all three once
        is correct -- and far better than silently hiding two forever."""
        store, results, _ = self._poisoned_setup(tmp_path)
        migrate.run(store, results)
        clusters, _, _ = dedupe.cluster(results)
        new = [c for c in clusters if not store.has_any(sorted(c.keys))]
        assert len(new) == 3

    def test_a_clean_key_is_translated(self, tmp_path):
        j = job(url="https://acme.wd5.myworkdayjobs.com/careers/job/NY/Intern_JR1234")
        legacy = canonical_legacy.canonical_url_key(j.apply_url)
        assert legacy == "wd:jr1234"                    # the old, tenant-less form

        path = tmp_path / "seen.json"
        path.write_text(json.dumps({"keys": {legacy: "2026-07-24T00:00:00+00:00"}}))
        store = SeenStore(path)

        report = migrate.run(store, [SourceResult("repo", [j])])
        assert report.legacy_keys_translated == 1
        assert "wd:acme:jr1234" in store.keys
        assert report.one_time_resends == 0


class TestTimestamps:
    def test_original_first_seen_is_preserved(self, tmp_path):
        """Re-stamping with today would push the 180-day prune horizon out and
        keep long-dead postings in the file for a year."""
        old = (datetime.now(timezone.utc) - timedelta(days=170)).isoformat()
        j = job(url="https://acme.wd5.myworkdayjobs.com/careers/job/NY/Intern_JR1234")
        path = tmp_path / "seen.json"
        path.write_text(json.dumps({"keys": {"wd:jr1234": old}}))
        store = SeenStore(path)

        migrate.run(store, [SourceResult("repo", [j])])
        assert store.keys["wd:acme:jr1234"] == old

        # Still inside the window now...
        assert store.prune(RETENTION_DAYS) == 0
        # ...and prunes on the original schedule, not 180 days from today.
        assert store.prune(160) >= 1


class TestSourceFailureGuard:
    def test_migration_refuses_when_a_source_failed(self, corpus_results, seen_snapshot):
        """A failed source contributes no jobs, so its stored keys cannot be
        translated and all of its postings would be re-emailed."""
        from src.main import _run_migration

        broken = list(corpus_results) + [SourceResult("LinkedIn", [], ok=False, error="HTTP 429")]
        code = _run_migration(broken, ["LinkedIn: HTTP 429"], seen_snapshot, preview=False)
        assert code == 1


class TestPreviewMode:
    def test_preview_does_not_mutate_the_store(self, corpus_results, seen_snapshot):
        before = dict(seen_snapshot.keys)
        report = migrate.run(seen_snapshot, corpus_results, apply=False)
        assert seen_snapshot.keys == before
        assert report.one_time_resends >= 0

    def test_preview_matches_what_applying_does(self, corpus_results, seen_snapshot, tmp_path):
        import shutil
        from tests.conftest import SEEN_SNAPSHOT

        preview = migrate.run(seen_snapshot, corpus_results, apply=False)

        other = tmp_path / "copy.json"
        shutil.copy(SEEN_SNAPSHOT, other)
        applied = migrate.run(SeenStore(other), corpus_results, apply=True)

        assert preview.one_time_resends == applied.one_time_resends
        assert preview.jobs_new_after == applied.jobs_new_after


class TestLegacyModuleIsFrozen:
    """canonical_legacy must keep reproducing the OLD keys exactly. If an edit
    to canonical.py leaked into it, the migration would map the wrong things.
    """

    GOLDEN = {
        "https://acme.wd1.myworkdayjobs.com/careers/job/NY/Intern_JR1234": "wd:jr1234",
        "https://other.wd5.myworkdayjobs.com/en-US/x/job/SF/Analyst_JR1234": "wd:jr1234",
        "https://careers-sig.icims.com/jobs/10717/job?mobile=true": "icims:10717",
        "https://careers-other.icims.com/jobs/10717/job": "icims:10717",
        "https://careers.acme.com/jobdetails?jobId=111": "url:careers.acme.com/jobdetails",
        "https://careers.acme.com/jobdetails?jobId=222": "url:careers.acme.com/jobdetails",
        "https://jobs.smartrecruiters.com/Canva/6000000001291655": "sr:6000000001291655",
        "https://jobs.jobvite.com/altamiracorps/job/oHqCAfw3": "jv:oHqCAfw3",
        "https://job-boards.greenhouse.io/virtu/jobs/8624424002": "gh:virtu:8624424002",
        "https://jobs.lever.co/palantir/f221738b-e97c-4ce3-a12a-17ada2b855e4":
            "lv:palantir:f221738b-e97c-4ce3-a12a-17ada2b855e4",
    }

    @pytest.mark.parametrize("url,expected", sorted(GOLDEN.items()))
    def test_legacy_keys_are_unchanged(self, url, expected):
        assert canonical_legacy.canonical_url_key(url) == expected

    def test_legacy_identity_key_has_three_fields(self):
        key = canonical_legacy.identity_key("Acme", "SWE Intern, Summer 2027", "NYC")
        assert key.count("|") == 2                    # old shape
        assert canonical.identity_key("Acme", "SWE Intern, Summer 2027", "NYC").count("|") == 4

    def test_legacy_location_still_has_the_us_substring_bug(self):
        """Proof this really is the old code: the bug that mangled Austin is
        still here, and must stay, or stored keys stop matching."""
        assert canonical_legacy.normalize_location("Austin, TX") == "a tin tx"
        assert canonical.normalize_location("Austin, TX") == "austin tx"


class TestLegacyWindowStaysClosed:
    """The read window is closed for good. Re-opening it hides new postings.

    Legacy keys are season-blind: canonical_legacy.identity_key takes only
    (company, title, location), and normalize_title strips seasons and years,
    so "SWE Intern - Summer 2027", "... (Winter 2027)" and a bare "SWE Intern"
    share one key. On 2026-08-15 that suppressed Notion's Summer 2027 posting
    -- scraped, filtered, then dropped against a key with no season in it --
    along with 56 others in the same run.
    """

    def test_the_shipped_config_does_not_reopen_the_window(self):
        from src.main import _legacy_window_open, CONFIG_PATH
        import yaml
        with open(CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh)
        assert not _legacy_window_open(cfg, datetime(2026, 8, 15)), (
            "config/sources.yaml re-enables dedup.legacy_keys_until. Those keys "
            "carry no season, so any company with an older seen posting has its "
            "new-season postings silently dropped."
        )

    def test_a_new_season_is_not_suppressed_by_an_older_posting(self):
        """The Notion case, as an assertion.

        The two postings differ only by season. Their current keys must differ;
        the legacy keys, which is what broke, must not be what we compare on.
        """
        summer = job(company="Notion", title="Software Engineer Intern - Summer 2027",
                     location="San Francisco, CA", season="Summer 2027")
        winter = job(company="Notion", title="Software Engineer Intern - Winter 2027",
                     location="San Francisco, CA", season="Winter 2027")
        assert set(canonical.keys_for(summer)) & set(canonical.keys_for(winter)) == set(), (
            "two different seasons share a current key"
        )
        # ...and this is the collision that made the window unsafe:
        assert (set(canonical_legacy.legacy_keys_for(summer))
                & set(canonical_legacy.legacy_keys_for(winter))), (
            "legacy keys no longer collide -- if this fails the module changed "
            "and this guard needs rewriting"
        )


class TestLegacyReadWindow:
    def test_open_window_consults_legacy_keys(self):
        from src.main import _legacy_window_open
        cfg = {"dedup": {"legacy_keys_until": "2026-09-15"}}
        assert _legacy_window_open(cfg, datetime(2026, 8, 13))

    def test_closed_window_ignores_them(self):
        from src.main import _legacy_window_open
        cfg = {"dedup": {"legacy_keys_until": "2026-09-15"}}
        assert not _legacy_window_open(cfg, datetime(2026, 9, 16))

    def test_absent_config_means_closed(self):
        from src.main import _legacy_window_open
        assert not _legacy_window_open({}, datetime(2026, 8, 13))

    def test_seen_keys_include_legacy_only_while_open(self):
        from src.main import _seen_keys
        j = job(url="https://acme.wd5.myworkdayjobs.com/careers/job/NY/Intern_JR1234")
        clusters, _, _ = dedupe.cluster([SourceResult("repo", [j])])
        group = clusters[0]
        assert "wd:jr1234" not in _seen_keys(group, False)
        assert "wd:jr1234" in _seen_keys(group, True)

    def test_legacy_keys_are_never_written(self, corpus_results, empty_store):
        """Writing them back would reintroduce the collisions they caused."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        for group in clusters:
            empty_store.add(sorted(group.keys))
        bare_workday = [k for k in empty_store.keys
                        if k.startswith("wd:") and k.count(":") == 1]
        assert bare_workday == []
