"""Tests for splitting a digest that Gmail would otherwise clip.

Gmail truncates a message body over ~102KB behind "[Message clipped]". It fails
quietly -- the email looks complete until you scroll -- so a clipped digest
withholds postings without any signal that it did.
"""

from src.main import GMAIL_CLIP_BYTES, _digest_messages
from src.models import Job


def jobs(n, indirect=False, title="Software Engineer Intern", prefix="co"):
    return [
        Job(company=f"Company {i}", title=f"{title} {i}",
            apply_url=f"https://job-boards.greenhouse.io/{prefix}{i}/jobs/{1000 + i}",
            source="SimplifyJobs", location="New York, NY",
            season="Summer 2027", salary="$9,000/mo", posted="Aug 12",
            indirect=indirect)
        for i in range(n)
    ]


class TestSmallDigestIsUnchanged:
    """The common case must keep behaving exactly as it did."""

    def test_one_email(self):
        msgs = _digest_messages(jobs(20), [], "label", "Aug 12", "PM")
        assert len(msgs) == 1

    def test_subject_has_no_part_marker(self):
        part = _digest_messages(jobs(20), [], "label", "Aug 12", "PM")[0]
        assert part.subject == "[Job Digest] 20 new postings - Aug 12 PM"

    def test_singular_wording_for_one_job(self):
        part = _digest_messages(jobs(1), [], "label", "Aug 12", "AM")[0]
        assert "1 new posting -" in part.subject

    def test_empty_digest_still_renders_one_email(self):
        msgs = _digest_messages([], [], "label", "Aug 12", "AM")
        assert len(msgs) == 1

    def test_a_digest_near_the_threshold_is_never_over_it(self):
        """~80 postings is the size of the first digest after adding the Pitt
        CSC feed, and it lands close to the limit -- whether it splits depends
        on entry length, but no part may ever exceed the threshold."""
        for part in _digest_messages(jobs(80), [], "label", "Aug 12", "PM"):
            assert len(part.html) <= GMAIL_CLIP_BYTES


class TestLargeDigestIsSplit:
    def test_it_splits(self):
        assert len(_digest_messages(jobs(600), [], "label", "Aug 12", "PM")) > 1

    def test_no_part_exceeds_gmails_limit(self):
        for part in _digest_messages(jobs(600), [], "label", "Aug 12", "PM"):
            assert len(part.html) <= GMAIL_CLIP_BYTES

    def test_every_job_appears_exactly_once_across_parts(self):
        js = jobs(600)
        combined = "".join(p.html for p in _digest_messages(js, [], "l", "Aug 12", "PM"))
        for j in js:
            assert combined.count(j.apply_url) == 1, j.apply_url

    def test_no_job_is_lost(self):
        js = jobs(600)
        msgs = _digest_messages(js, [], "l", "Aug 12", "PM")
        combined = "".join(p.html for p in msgs)
        assert all(j.apply_url in combined for j in js)

    def test_subjects_carry_the_true_total_and_part_numbers(self):
        msgs = _digest_messages(jobs(600), [], "l", "Aug 12", "PM")
        total = len(msgs)
        for idx, part in enumerate(msgs, 1):
            assert "600 new postings" in part.subject
            assert f"({idx}/{total})" in part.subject

    def test_source_failures_are_reported_once_not_per_part(self):
        msgs = _digest_messages(jobs(600), ["sndsh404: HTTP 404"], "l", "Aug 12", "PM")
        assert sum(1 for p in msgs if "sndsh404" in p.html) == 1

    def test_linkedin_jobs_stay_in_their_own_section(self):
        mixed = jobs(300) + jobs(300, indirect=True, prefix="li")
        msgs = _digest_messages(mixed, [], "l", "Aug 12", "PM")
        combined = "".join(p.html for p in msgs)
        assert combined.count("Direct Apply") >= 1
        for j in mixed:
            assert combined.count(j.apply_url) == 1

    def test_plaintext_alternative_is_also_split(self):
        msgs = _digest_messages(jobs(600), [], "l", "Aug 12", "PM")
        assert all(p.text.strip() for p in msgs)
        combined = "".join(p.text for p in msgs)
        for j in jobs(600):
            assert j.apply_url in combined


class TestMaybeSection:
    """Borderline postings are demoted to their own section, never deleted, so
    an over-eager filter rule is visible rather than silently costing a job."""

    def maybe(self, n, rule="maybe_titles"):
        js = jobs(n, prefix="mb")
        for j in js:
            j.relevance = "maybe"
            j.relevance_rule = rule
        return js

    def test_the_section_renders(self):
        part = _digest_messages(jobs(3) + self.maybe(2), [], "l", "Aug 13", "AM")[0]
        assert "Maybe" in part.html
        assert "Maybe" in part.text

    def test_the_firing_rule_is_shown(self):
        part = _digest_messages(self.maybe(1), [], "l", "Aug 13", "AM")[0]
        assert "maybe_titles" in part.html

    def test_maybe_jobs_are_excluded_from_the_headline_count(self):
        part = _digest_messages(jobs(3) + self.maybe(2), [], "l", "Aug 13", "AM")[0]
        assert "3 new postings" in part.subject
        assert "(+2 maybe)" in part.subject

    def test_a_digest_of_only_maybes_still_sends(self):
        part = _digest_messages(self.maybe(2), [], "l", "Aug 13", "AM")[0]
        assert "0 new postings" in part.subject
        assert "(+2 maybe)" in part.subject

    def test_maybe_jobs_are_still_delivered_and_recorded(self):
        js = jobs(3) + self.maybe(2)
        parts = _digest_messages(js, [], "l", "Aug 13", "AM")
        delivered = [j for p in parts for j in p.jobs]
        assert len(delivered) == 5

    def test_maybe_jobs_land_in_the_last_part_when_split(self):
        js = jobs(600) + self.maybe(40)
        parts = _digest_messages(js, [], "l", "Aug 13", "AM")
        assert len(parts) > 1
        maybe_parts = {i for i, p in enumerate(parts)
                       if any(j.relevance == "maybe" for j in p.jobs)}
        assert max(maybe_parts) == len(parts) - 1

    def test_no_part_exceeds_the_limit_with_a_maybe_section(self):
        for part in _digest_messages(jobs(600) + self.maybe(200), [], "l", "Aug 13", "AM"):
            assert len(part.html) <= GMAIL_CLIP_BYTES

    def test_every_job_still_appears_exactly_once(self):
        js = jobs(400) + self.maybe(60)
        combined = "".join(p.html for p in _digest_messages(js, [], "l", "Aug 13", "AM"))
        for j in js:
            assert combined.count(j.apply_url) == 1, j.apply_url


class TestMultiCityRoles:
    """One requisition per office is normal -- IBM's Spring Co-op was live in
    four cities on 2026-08-13. They are separate applications, so dedupe keeps
    them apart; they are merged for DISPLAY only."""

    def cities(self):
        return [
            Job(company="IBM", title="Software Developer Spring Co-op 2027",
                apply_url=f"https://www.linkedin.com/jobs/view/44504430{i}",
                source="LinkedIn", location=loc, season="Spring 2027")
            for i, loc in enumerate(["Lowell, MA", "Durham, NC", "San Jose, CA", "Austin, TX"])
        ]

    def test_one_row_not_four(self):
        part = _digest_messages(self.cities(), [], "l", "Aug 13", "AM")[0]
        assert part.html.count("Software Developer Spring Co-op 2027") == 1

    def test_every_city_is_listed(self):
        part = _digest_messages(self.cities(), [], "l", "Aug 13", "AM")[0]
        for city in ("Lowell", "Durham", "San Jose", "Austin"):
            assert city in part.html

    def test_every_apply_link_survives(self):
        js = self.cities()
        part = _digest_messages(js, [], "l", "Aug 13", "AM")[0]
        for j in js:
            assert j.apply_url in part.html

    def test_all_four_postings_are_still_recorded(self):
        """Display grouping must never reduce what gets marked seen."""
        js = self.cities()
        parts = _digest_messages(js, [], "l", "Aug 13", "AM")
        assert len([j for p in parts for j in p.jobs]) == 4

    def test_different_titles_stay_separate(self):
        js = self.cities()
        js[0].title = "Backend Developer Spring Co-op 2027"
        part = _digest_messages(js, [], "l", "Aug 13", "AM")[0]
        assert part.html.count("Spring Co-op 2027") == 2

    def test_plaintext_lists_each_city(self):
        part = _digest_messages(self.cities(), [], "l", "Aug 13", "AM")[0]
        assert part.text.count("[Lowell, MA]") == 1
        assert part.text.count("linkedin.com/jobs/view") == 4


class TestPartsCarryTheirJobs:
    """A part records what it delivered, so a failure on part 3 of 4 re-sends
    part 3 onward and nothing else."""

    def test_a_single_part_carries_every_job(self):
        js = jobs(20)
        assert _digest_messages(js, [], "l", "Aug 13", "AM")[0].jobs == js

    def test_split_parts_partition_the_jobs(self):
        js = jobs(600)
        parts = _digest_messages(js, [], "l", "Aug 13", "AM")
        delivered = [j for p in parts for j in p.jobs]
        assert len(delivered) == len(js)
        assert {id(j) for j in delivered} == {id(j) for j in js}

    def test_a_parts_jobs_match_its_html(self):
        parts = _digest_messages(jobs(600), [], "l", "Aug 13", "AM")
        for part in parts:
            for job in part.jobs:
                assert job.apply_url in part.html
