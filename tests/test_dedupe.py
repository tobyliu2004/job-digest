"""Deduplication: no duplicates in an email, and none across emails.

These are the two properties the digest lives or dies by, so the tests are
split into the two ways they can break:

  under-merge -> the same job appears twice. Annoying, and visible.
  over-merge  -> two different jobs share a key, so one is marked seen without
                 ever being sent and is suppressed forever. Invisible.

The over-merge tests matter more, because that failure is silent.
"""

from __future__ import annotations

import pytest

from src import canonical, dedupe
from src.models import Job, SourceResult


def job(company="Acme", title="Software Engineer Intern", url="", source="repo",
        location="New York, NY", season="Summer 2027", indirect=False, **extra):
    return Job(company=company, title=title, apply_url=url, source=source,
               location=location, season=season, indirect=indirect,
               extra=extra or {})


def one(*jobs, name="src"):
    return SourceResult(name, list(jobs))


class TestTransitiveClustering:
    """A collapsed job's keys must join the cluster. The old code recorded only
    the survivor's keys, so an A-B-C chain leaked C as a duplicate."""

    def test_chain_collapses_to_one(self):
        a = job(url="https://job-boards.greenhouse.io/acme/jobs/1")
        # b shares a's identity key but has its own URL...
        b = job(url="https://boards.greenhouse.io/acme/jobs/1")
        # ...and c shares b's URL key.
        c = job(title="SWE Intern", url="https://boards.greenhouse.io/acme/jobs/1")
        clusters, collapsed, _ = dedupe.cluster([one(a), one(b), one(c)])
        assert len(clusters) == 1
        assert collapsed == 2

    def test_cluster_carries_every_members_keys(self):
        a = job(url="https://job-boards.greenhouse.io/acme/jobs/1")
        b = job(title="SWE Intern", url="https://job-boards.greenhouse.io/acme/jobs/1")
        clusters, _, _ = dedupe.cluster([one(a), one(b)])
        keys = clusters[0].keys
        assert set(canonical.keys_for(a)) <= keys
        assert set(canonical.keys_for(b)) <= keys

    def test_a_merged_member_is_never_resent(self, empty_store):
        """Store the cluster union, then re-run: nothing may look new."""
        a = job(url="https://job-boards.greenhouse.io/acme/jobs/1")
        b = job(title="SWE Intern", url="https://job-boards.greenhouse.io/acme/jobs/1")
        clusters, _, _ = dedupe.cluster([one(a), one(b)])
        for group in clusters:
            empty_store.add(sorted(group.keys))

        again, _, _ = dedupe.cluster([one(a), one(b)])
        assert not [c for c in again if not empty_store.has_any(sorted(c.keys))]

    def test_bridging_job_merges_two_existing_clusters(self):
        """A job with no requisition id of its own can legitimately link two
        clusters -- that is transitivity doing its job."""
        a = job(company="Acme", title="Backend Intern",
                url="https://job-boards.greenhouse.io/acme/jobs/1")
        b = job(company="Acme", title="Backend Intern",
                url="https://careers.acme.com/roles/backend")
        # No ATS id, so nothing contradicts a title match on either side.
        bridge = job(company="Acme", title="Backend Intern",
                     url="https://careers.acme.com/roles/backend?utm_source=x")
        clusters, _, collapsed_count = dedupe.cluster([one(a), one(b), one(bridge)])
        assert len(clusters) == 1
        assert len(clusters[0].members) == 3

    def test_conflicting_requisition_ids_block_a_bridge(self):
        """The mirror image: two real Greenhouse ids must not be chained
        together just because a third row shares one of their titles."""
        a = job(company="Acme", title="Backend Intern",
                url="https://job-boards.greenhouse.io/acme/jobs/1")
        b = job(company="Acme", title="Frontend Intern",
                url="https://job-boards.greenhouse.io/acme/jobs/2")
        bridge = job(company="Acme", title="Frontend Intern",
                     url="https://job-boards.greenhouse.io/acme/jobs/1")
        clusters, _, _ = dedupe.cluster([one(a), one(b), one(bridge)])
        assert len(clusters) == 2


class TestStrongIdVetoesWeakMatch:
    """TikTok posts six separate San Jose "Software Engineer Intern" roles with
    byte-identical titles. Only the requisition id separates them, so a title
    match must not be allowed to merge them."""

    def test_identical_titles_with_different_ats_ids_stay_separate(self):
        jobs = [job(company="TikTok", title="Software Engineer Intern",
                    location="San Jose, CA",
                    url=f"https://lifeattiktok.com/search/76728418408609405{i}")
                for i in range(6)]
        clusters, collapsed, _ = dedupe.cluster([one(*jobs)])
        assert len(clusters) == 6
        assert collapsed == 0

    def test_same_ats_id_still_merges(self):
        a = job(url="https://lifeattiktok.com/search/7672881840860940597", source="A")
        b = job(url="https://lifeattiktok.com/search/7672881840860940597", source="B")
        clusters, _, _ = dedupe.cluster([one(a, name="A"), one(b, name="B")])
        assert len(clusters) == 1

    def test_title_match_still_merges_when_one_side_has_no_ats_id(self):
        """The veto needs hard evidence on BOTH sides. A Simplify click stub
        has no requisition id, so it must still join its employer link."""
        direct = job(url="https://job-boards.greenhouse.io/acme/jobs/1")
        stub = job(url="https://simplify.jobs/jobs/click/abc", source="Simplify")
        clusters, collapsed, _ = dedupe.cluster([one(direct), one(stub, name="Simplify")])
        assert len(clusters) == 1
        assert collapsed == 1

    def test_veto_does_not_apply_across_a_shared_uuid(self):
        """A shared Simplify UUID is tier 0 -- stronger than either ATS id."""
        uuid = "de926b0a-99e7-4dbd-94cd-334ec5600000"
        feed = job(url="https://citadel.com/careers/1", feed_id=uuid)
        index = job(url="https://simplify.jobs/jobs/click/" + uuid, simplify_id=uuid)
        clusters, _, _ = dedupe.cluster([one(feed), one(index, name="Simplify")])
        assert len(clusters) == 1


class TestSurvivorSelection:
    def test_direct_link_beats_linkedin(self):
        direct = job(url="https://job-boards.greenhouse.io/acme/jobs/1", source="repo")
        li = job(url="https://www.linkedin.com/jobs/view/4444729829",
                 source="LinkedIn", indirect=True)
        clusters, _, _ = dedupe.cluster([
            SourceResult("LinkedIn", [li]), SourceResult("repo", [direct]),
        ])
        assert len(clusters) == 1
        assert "greenhouse" in clusters[0].job.apply_url

    def test_unusable_jobs_are_counted_not_silently_dropped(self):
        clusters, _, unusable = dedupe.cluster([one(job(company="", title="", url=""))])
        assert clusters == []
        assert unusable == 1


class TestSeasonAndLevelSeparation:
    """The identity key used to erase season and level, so Summer and Fall of
    the same role were one key -- and so were an intern and a new-grad role."""

    def test_summer_and_fall_are_different_jobs(self):
        summer = job(title="SWE Intern, Summer 2027", season="Summer 2027",
                     url="https://careers.acme.com/a")
        fall = job(title="SWE Intern, Fall 2027", season="Fall 2027",
                   url="https://careers.acme.com/b")
        clusters, _, _ = dedupe.cluster([one(summer, fall)])
        assert len(clusters) == 2

    def test_intern_and_new_grad_are_different_jobs(self):
        intern = job(title="Software Engineer Intern", url="https://careers.acme.com/a")
        grad = job(title="Software Engineer, New Grad", url="https://careers.acme.com/b")
        clusters, _, _ = dedupe.cluster([one(intern, grad)])
        assert len(clusters) == 2

    def test_a_new_grad_row_cannot_hide_an_internship(self):
        """The exact silent-loss bug. Filtering runs before dedupe, so a
        new-grad row can no longer win a collapse and then be filtered out,
        leaving the internship emitted by neither."""
        from src.main import filter_results, is_internship

        grad = job(title="Software Engineer, New Grad", source="repoA",
                   url="https://repo-a.example.com/1")
        intern = job(title="Software Engineer Intern, Summer 2027", source="repoB",
                     url="https://repo-b.example.com/1")
        results = [SourceResult("repoA", [grad]), SourceResult("repoB", [intern])]

        filtered, dropped = filter_results(results, is_internship)
        clusters, _, _ = dedupe.cluster(filtered)

        assert dropped == 1
        titles = [c.job.title for c in clusters]
        assert titles == ["Software Engineer Intern, Summer 2027"]

    def test_degree_and_wording_variants_still_merge(self):
        a = job(title="SWE Intern - BS - Summer 2027", season="Summer 2027",
                url="https://a.example.com/1")
        b = job(title="SWE Intern, Summer 2027", season="Summer 2027",
                url="https://b.example.com/1")
        clusters, _, _ = dedupe.cluster([one(a, b)])
        assert len(clusters) == 1


class TestAgainstTheLiveCorpus:
    """Invariants over a real 1,407-job scrape. These are the guards that catch
    a future change quietly merging or splitting the world."""

    def test_is_idempotent(self, corpus_results):
        first, c1, _ = dedupe.cluster(corpus_results)
        second, c2, _ = dedupe.cluster(corpus_results)
        assert (len(first), c1) == (len(second), c2)

    def test_every_job_is_accounted_for(self, corpus_results, corpus_jobs):
        """No job may vanish unexplained: each is a survivor, a merged member,
        or explicitly unusable."""
        clusters, collapsed, unusable = dedupe.cluster(corpus_results)
        members = sum(len(c.members) for c in clusters)
        assert members + unusable == len(corpus_jobs)
        assert len(clusters) + collapsed == members

    def test_collapse_rate_stays_in_range(self, corpus_results, corpus_jobs):
        """~40% of scraped rows are cross-source duplicates. A large move in
        either direction means dedup semantics changed -- deliberately or not."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        ratio = len(clusters) / len(corpus_jobs)
        assert 0.50 <= ratio <= 0.75, f"{len(clusters)}/{len(corpus_jobs)} = {ratio:.2f}"

    def test_no_cluster_is_implausibly_large(self, corpus_results):
        """Seven sources means at most a handful of copies of one posting. A
        big cluster is over-merge -- unrelated jobs chained onto one key."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        worst = max(clusters, key=lambda c: len(c.members))
        assert len(worst.members) <= 10, (
            f"{len(worst.members)} jobs merged into {worst.job.company} "
            f"- {worst.job.title}"
        )

    def test_no_two_clusters_share_a_key(self, corpus_results):
        """Clusters must partition the key space; an overlap means the union
        step failed and the same job could be stored under two identities."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        seen: dict[str, int] = {}
        for i, group in enumerate(clusters):
            for key in group.keys:
                assert key not in seen, f"{key} in clusters {seen[key]} and {i}"
                seen[key] = i

    def test_merged_requisition_ids_stay_few(self, corpus_results):
        """A cluster may legitimately span two employer ATS ids -- Chicago
        Trading posts the same role to two Greenhouse boards -- but a cluster
        chaining several distinct requisitions is over-merge."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        for group in clusters:
            ids = {canonical.employer_ats_key(m.apply_url) for m in group.members}
            ids.discard("")
            assert len(ids) <= 2, (
                f"{group.job.company} - {group.job.title} merged {len(ids)} "
                f"requisition ids: {sorted(ids)}"
            )

    def test_second_run_emits_nothing(self, corpus_results, empty_store):
        """Store one run, replay it: a correct pipeline sends zero the second
        time. This is the cross-email duplicate guarantee."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        for group in clusters:
            empty_store.add(sorted(group.keys))

        again, _, _ = dedupe.cluster(corpus_results)
        still_new = [c for c in again if not empty_store.has_any(sorted(c.keys))]
        assert still_new == []

    def test_no_duplicate_apply_url_survives(self, corpus_results):
        """Two clusters must never carry the same apply URL -- that is a plain
        duplicate in the email."""
        clusters, _, _ = dedupe.cluster(corpus_results)
        urls = [c.job.apply_url for c in clusters if c.job.apply_url]
        assert len(urls) == len(set(urls))


@pytest.mark.parametrize("field", ["company", "title"])
def test_a_job_missing_identity_still_dedupes_on_url(field):
    kwargs = {field: ""}
    a = job(url="https://job-boards.greenhouse.io/acme/jobs/9", **kwargs)
    b = job(url="https://job-boards.greenhouse.io/acme/jobs/9?utm_source=x", **kwargs)
    clusters, collapsed, _ = dedupe.cluster([one(a, b)])
    assert len(clusters) == 1
    assert collapsed == 1
