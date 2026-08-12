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
        subject, _, _ = _digest_messages(jobs(20), [], "label", "Aug 12", "PM")[0]
        assert subject == "[Job Digest] 20 new postings - Aug 12 PM"

    def test_singular_wording_for_one_job(self):
        subject, _, _ = _digest_messages(jobs(1), [], "label", "Aug 12", "AM")[0]
        assert "1 new posting -" in subject

    def test_empty_digest_still_renders_one_email(self):
        msgs = _digest_messages([], [], "label", "Aug 12", "AM")
        assert len(msgs) == 1

    def test_a_digest_near_the_threshold_is_never_over_it(self):
        """~80 postings is the size of the first digest after adding the Pitt
        CSC feed, and it lands close to the limit -- whether it splits depends
        on entry length, but no part may ever exceed the threshold."""
        for _, html, _ in _digest_messages(jobs(80), [], "label", "Aug 12", "PM"):
            assert len(html) <= GMAIL_CLIP_BYTES


class TestLargeDigestIsSplit:
    def test_it_splits(self):
        assert len(_digest_messages(jobs(600), [], "label", "Aug 12", "PM")) > 1

    def test_no_part_exceeds_gmails_limit(self):
        for _, html, _ in _digest_messages(jobs(600), [], "label", "Aug 12", "PM"):
            assert len(html) <= GMAIL_CLIP_BYTES

    def test_every_job_appears_exactly_once_across_parts(self):
        js = jobs(600)
        combined = "".join(h for _, h, _ in _digest_messages(js, [], "l", "Aug 12", "PM"))
        for j in js:
            assert combined.count(j.apply_url) == 1, j.apply_url

    def test_no_job_is_lost(self):
        js = jobs(600)
        msgs = _digest_messages(js, [], "l", "Aug 12", "PM")
        combined = "".join(h for _, h, _ in msgs)
        assert all(j.apply_url in combined for j in js)

    def test_subjects_carry_the_true_total_and_part_numbers(self):
        msgs = _digest_messages(jobs(600), [], "l", "Aug 12", "PM")
        total = len(msgs)
        for idx, (subject, _, _) in enumerate(msgs, 1):
            assert "600 new postings" in subject
            assert f"({idx}/{total})" in subject

    def test_source_failures_are_reported_once_not_per_part(self):
        msgs = _digest_messages(jobs(600), ["sndsh404: HTTP 404"], "l", "Aug 12", "PM")
        assert sum(1 for _, h, _ in msgs if "sndsh404" in h) == 1

    def test_linkedin_jobs_stay_in_their_own_section(self):
        mixed = jobs(300) + jobs(300, indirect=True, prefix="li")
        msgs = _digest_messages(mixed, [], "l", "Aug 12", "PM")
        combined = "".join(h for _, h, _ in msgs)
        assert combined.count("Direct Apply") >= 1
        for j in mixed:
            assert combined.count(j.apply_url) == 1

    def test_plaintext_alternative_is_also_split(self):
        msgs = _digest_messages(jobs(600), [], "l", "Aug 12", "PM")
        assert all(text.strip() for _, _, text in msgs)
        combined = "".join(t for _, _, t in msgs)
        for j in jobs(600):
            assert j.apply_url in combined
