"""End-to-end: the whole pipeline over a real 1,407-job scrape.

The single most valuable assertion in the suite is
test_every_job_is_accounted_for. Every bug this audit found -- tenant-less
Workday keys, the discarded query string, the erased season, the non-transitive
collapse -- had the same shape: a job disappeared with nothing to show for it.
Requiring every input job to end up in exactly one named bucket catches that
whole class, including the next one nobody has thought of.
"""

from __future__ import annotations

import pytest
import yaml

from src import canonical, dedupe, relevance
from src.main import (CONFIG_PATH, _digest_messages, filter_results,
                      is_internship)
from src.models import Job, SourceResult


@pytest.fixture(scope="module")
def config():
    with open(CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def run_pipeline(results, config, store=None):
    """The exact sequence main() uses, minus I/O."""
    judge = relevance.load(config)
    dropped_relevance = [j for r in results for j in r.jobs
                         if judge.judge(j).action == relevance.DROP]

    kept, _ = filter_results(results, judge.tag)
    dropped_intern = []
    if config.get("intern_only"):
        before = {id(j) for r in kept for j in r.jobs}
        kept, _ = filter_results(kept, is_internship)
        after = {id(j) for r in kept for j in r.jobs}
        dropped_intern = [j for r in results for j in r.jobs
                          if id(j) in before - after]

    clusters, collapsed, unusable = dedupe.cluster(kept)
    new = clusters
    already_seen = []
    if store is not None:
        new = [c for c in clusters if not store.has_any(sorted(c.keys))]
        already_seen = [c for c in clusters if store.has_any(sorted(c.keys))]

    return {
        "clusters": clusters, "new": new, "already_seen": already_seen,
        "collapsed": collapsed, "unusable": unusable,
        "dropped_relevance": dropped_relevance, "dropped_intern": dropped_intern,
    }


class TestNothingVanishes:
    def test_every_job_is_accounted_for(self, fresh_results, corpus_jobs, config):
        """Each input job is exactly one of: emitted, merged into a cluster,
        relevance-dropped, intern-filtered, or structurally unusable."""
        out = run_pipeline(fresh_results, config)

        emitted = len(out["clusters"])
        merged = out["collapsed"]
        explained = (emitted + merged + out["unusable"]
                     + len(out["dropped_relevance"]) + len(out["dropped_intern"]))
        assert explained == len(corpus_jobs), (
            f"{len(corpus_jobs) - explained} job(s) vanished unexplained "
            f"(emitted={emitted} merged={merged} unusable={out['unusable']} "
            f"relevance={len(out['dropped_relevance'])} "
            f"intern={len(out['dropped_intern'])})"
        )

    def test_most_of_the_corpus_survives(self, fresh_results, corpus_jobs, config):
        """A filter bug that eats the digest shows up here."""
        out = run_pipeline(fresh_results, config)
        assert len(out["clusters"]) >= 0.4 * len(corpus_jobs)

    def test_every_emitted_job_has_an_apply_url(self, fresh_results, config):
        out = run_pipeline(fresh_results, config)
        assert all(c.job.apply_url for c in out["clusters"])

    def test_every_emitted_cluster_has_at_least_one_key(self, fresh_results, config):
        """A keyless cluster could never be recorded, so it would be emailed
        again on every single run."""
        out = run_pipeline(fresh_results, config)
        assert all(c.keys for c in out["clusters"])


class TestNoDuplicatesWithinAnEmail:
    def test_no_apply_url_appears_twice(self, fresh_results, config):
        out = run_pipeline(fresh_results, config)
        urls = [c.job.apply_url for c in out["clusters"]]
        assert len(urls) == len(set(urls))

    def test_no_two_emitted_jobs_share_a_key(self, fresh_results, config):
        out = run_pipeline(fresh_results, config)
        seen: dict[str, str] = {}
        for group in out["clusters"]:
            for key in group.keys:
                assert key not in seen, f"{key}: {seen[key]} vs {group.job.title}"
                seen[key] = group.job.title

    def test_the_rendered_email_lists_each_url_once(self, fresh_results, config):
        import html as html_mod

        out = run_pipeline(fresh_results, config)
        jobs = [c.job for c in out["clusters"]]
        body = "".join(p.html for p in _digest_messages(jobs, [], "l", "Aug 13", "AM"))
        for job in jobs[:150]:                       # a sample keeps this quick
            # Escaped, because a query string's & renders as &amp;.
            assert body.count(html_mod.escape(job.apply_url, quote=True)) == 1, job.apply_url


class TestNoDuplicatesAcrossEmails:
    def test_a_second_run_emits_nothing(self, fresh_results, corpus_results,
                                        config, empty_store):
        """The cross-email guarantee: store one run's keys, replay the same
        scrape, and nothing may be sent again."""
        first = run_pipeline(fresh_results, config)
        for group in first["clusters"]:
            empty_store.add(sorted(group.keys))

        second = run_pipeline(corpus_results, config, store=empty_store)
        assert second["new"] == []
        assert len(second["already_seen"]) == len(first["clusters"])

    def test_a_third_run_after_storing_is_still_empty(self, fresh_results,
                                                      corpus_results, config,
                                                      empty_store):
        for group in run_pipeline(fresh_results, config)["clusters"]:
            empty_store.add(sorted(group.keys))
        run_pipeline(corpus_results, config, store=empty_store)
        assert run_pipeline(corpus_results, config, store=empty_store)["new"] == []

    def test_a_url_that_changes_shape_is_not_resent(self, empty_store):
        """A Simplify click stub is resolved to the employer URL after
        clustering. Both forms must be recorded or it looks new tomorrow."""
        uuid = "de926b0a-99e7-4dbd-94cd-334ec5600000"
        stub = Job(company="Citadel", title="Data Scientist Intern",
                   apply_url=f"https://simplify.jobs/jobs/click/{uuid}",
                   source="Simplify", location="NYC", season="Summer 2027",
                   extra={"simplify_id": uuid})
        clusters, _, _ = dedupe.cluster([SourceResult("Simplify", [stub])])
        keys = sorted(clusters[0].keys)

        stub.apply_url = "https://www.citadel.com/careers/details/data-scientist"
        empty_store.add(sorted(set(keys) | set(canonical.keys_for(stub))))

        again, _, _ = dedupe.cluster([SourceResult("Simplify", [stub])])
        assert empty_store.has_any(sorted(again[0].keys))

    def test_a_failed_source_does_not_mark_its_jobs_seen(self, config, empty_store):
        """Jobs from a source that errored must reappear next run."""
        good = Job("Acme", "SWE Intern", "https://job-boards.greenhouse.io/a/jobs/1", "repo")
        results = [SourceResult("repo", [good]),
                   SourceResult("LinkedIn", [], ok=False, error="HTTP 429")]
        out = run_pipeline(results, config, store=empty_store)
        for group in out["new"]:
            empty_store.add(sorted(group.keys))

        # LinkedIn recovers and returns the job it could not fetch before.
        li = Job("Beta", "SWE Intern", "https://www.linkedin.com/jobs/view/4444729829",
                 "LinkedIn", indirect=True)
        recovered = run_pipeline(
            [SourceResult("repo", [good]), SourceResult("LinkedIn", [li])],
            config, store=empty_store,
        )
        assert [c.job.company for c in recovered["new"]] == ["Beta"]


class TestFilterOrdering:
    def test_filters_run_before_dedupe(self, fresh_results, config):
        """If dedupe ran first, a filtered-out row could win a collapse and
        take a real internship down with it."""
        out = run_pipeline(fresh_results, config)
        for group in out["clusters"]:
            for member in group.members:
                assert is_internship(member), member.title

    def test_no_hard_dropped_job_reaches_the_email(self, fresh_results, config):
        out = run_pipeline(fresh_results, config)
        dropped_urls = {j.apply_url for j in out["dropped_relevance"]}
        emitted_urls = {c.job.apply_url for c in out["clusters"]}
        assert not (dropped_urls & emitted_urls)

    def test_maybe_jobs_do_reach_the_email(self, fresh_results, config):
        """Demoted, not deleted -- that is the entire point of the Maybe
        section."""
        out = run_pipeline(fresh_results, config)
        maybes = [c for c in out["clusters"] if c.job.relevance == relevance.MAYBE]
        assert maybes, "expected some borderline postings in the corpus"


class TestStorageDiscipline:
    def test_dropped_jobs_are_never_stored(self, fresh_results, config, empty_store):
        """Dropped jobs cost nothing to re-judge, so loosening a rule later
        must bring them straight back."""
        out = run_pipeline(fresh_results, config, store=empty_store)
        for group in out["new"]:
            empty_store.add(sorted(group.keys))

        for job in out["dropped_relevance"]:
            assert not empty_store.has_any(canonical.keys_for(job)), job.title

    def test_maybe_jobs_are_stored(self, fresh_results, config, empty_store):
        """They were shown to you, so they must not come back tomorrow."""
        out = run_pipeline(fresh_results, config, store=empty_store)
        for group in out["new"]:
            empty_store.add(sorted(group.keys))

        maybes = [c for c in out["clusters"] if c.job.relevance == relevance.MAYBE]
        assert all(empty_store.has_any(sorted(c.keys)) for c in maybes)
