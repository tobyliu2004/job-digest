"""Tests for the GitHub markdown parser and the store's safety behaviours.

Table fixtures are trimmed from the real repo files, preserving the quirks
that actually broke naive parsing during development.
"""

import json

from src.scrapers.github_md import parse_markdown
from src.store import SeenStore

VANSH = """
| Company | Role | Location | Application/Link | Date Posted |
| ------- | ---- | -------- | ---------------- | ----------- |
| Palantir Technologies | Software Engineer Intern, Production Infrastructure | New York, NY | <a href="https://jobs.lever.co/palantir/37964982-9b4c-471e-a1d8-fb8f45d7f116?utm_source=github-vansh-ouckah"><img src="https://i.imgur.com/u1KNU8z.png" width="118" alt="Apply"></a> | Jul 24 |
| ↳ | Software Engineer Intern, Infrastructure | Palo Alto, CA | <a href="https://jobs.lever.co/palantir/f221738b-e97c-4ce3-a12a-17ada2b855e4"><img src="https://i.imgur.com/u1KNU8z.png" width="118" alt="Apply"></a> | Jul 24 |
| ↳ | Forward Deployed Software Engineer Intern | Chicago, IL | <a href="https://jobs.lever.co/palantir/d5486403-c050-4920-b2e0-91b69b61ebb2"><img src="https://i.imgur.com/u1KNU8z.png" width="118" alt="Apply"></a> | Jul 24 |
| Tesla 🔒 | Closed Role | Fremont, CA | <a href="https://www.tesla.com/careers/search/job/999"><img src="https://i.imgur.com/u1KNU8z.png" alt="Apply"></a> | Jul 20 |
"""

SPEEDY = """
| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.google.com"><strong>Google</strong></a> | Software Engineering Intern - BS - Summer 2027 | Mountain View, CA +29 | $72/hr | <a href="https://www.google.com/about/careers/applications/jobs/results/85564713261245126"><img src="https://i.imgur.com/JpkfjIq.png" alt="Apply" width="70"/></a> | 3d |
"""

SNDSH = """
| Company | Role | Location | Apply | Added |
| --- | --- | --- | --- | --- |
| Susquehanna | Quantitative Systematic Trading Intern (PhD, Summer 2027) | New York, NY | [apply](https://careers.sig.com/jobs/10822) | 2026-07-21 |
"""


class TestCarryForward:
    def test_arrow_inherits_company_from_row_above(self):
        jobs = parse_markdown(VANSH, "vansh")
        assert [j.company for j in jobs] == ["Palantir Technologies"] * 3

    def test_arrow_rows_keep_their_own_titles_and_links(self):
        jobs = parse_markdown(VANSH, "vansh")
        assert jobs[1].title == "Software Engineer Intern, Infrastructure"
        assert jobs[2].apply_url.endswith("d5486403-c050-4920-b2e0-91b69b61ebb2")

    def test_closed_roles_are_skipped(self):
        jobs = parse_markdown(VANSH, "vansh")
        assert all("Closed Role" not in j.title for j in jobs)


class TestColumnMapping:
    def test_speedyapply_company_is_text_not_the_corporate_href(self):
        """The company cell links to google.com; the apply link is elsewhere."""
        jobs = parse_markdown(SPEEDY, "speedy")
        assert len(jobs) == 1
        assert jobs[0].company == "Google"
        assert "about/careers" in jobs[0].apply_url
        assert jobs[0].apply_url != "https://www.google.com"

    def test_speedyapply_salary_column_captured(self):
        assert parse_markdown(SPEEDY, "speedy")[0].salary == "$72/hr"

    def test_markdown_style_apply_link(self):
        jobs = parse_markdown(SNDSH, "sndsh")
        assert jobs[0].apply_url == "https://careers.sig.com/jobs/10822"
        assert jobs[0].company == "Susquehanna"

    def test_decorative_links_never_become_apply_urls(self):
        for markdown, name in ((VANSH, "v"), (SPEEDY, "s")):
            for job in parse_markdown(markdown, name):
                assert "imgur" not in job.apply_url


class TestStore:
    def test_new_only_when_all_keys_miss(self, tmp_path):
        store = SeenStore(tmp_path / "seen.json")
        store.add(["gh:acme:1", "id:acme|swe|nyc"])
        # Either key matching means "already sent".
        assert store.has_any(["gh:acme:1", "id:other|x|y"])
        assert store.has_any(["url:unknown", "id:acme|swe|nyc"])
        assert not store.has_any(["gh:acme:2", "id:acme|data|sf"])

    def test_roundtrip_persists_keys(self, tmp_path):
        path = tmp_path / "seen.json"
        store = SeenStore(path)
        assert store.was_empty
        store.add(["gh:acme:1"])
        store.save()

        reloaded = SeenStore(path)
        assert not reloaded.was_empty
        assert reloaded.has_any(["gh:acme:1"])

    def test_corrupt_state_is_treated_as_first_run_not_as_empty_success(self, tmp_path):
        """A truncated file must trigger the first-run guard, not a mass re-send."""
        path = tmp_path / "seen.json"
        path.write_text('{"keys": {"gh:a:1": ')  # truncated
        store = SeenStore(path)
        assert store.was_empty is True

    def test_save_is_atomic_and_leaves_no_temp_files(self, tmp_path):
        path = tmp_path / "seen.json"
        store = SeenStore(path)
        store.add(["gh:acme:1"])
        store.save()
        assert json.loads(path.read_text())["count"] == 1
        assert list(tmp_path.glob("*.tmp")) == []

    def test_prune_drops_only_old_entries(self, tmp_path):
        store = SeenStore(tmp_path / "seen.json")
        store.add(["fresh"])
        store.keys["ancient"] = "2000-01-01T00:00:00+00:00"
        store.prune(days=180)
        assert "fresh" in store.keys
        assert "ancient" not in store.keys


class TestSkippedSections:
    """Not every table in these READMEs is a job list. sndsh404 keeps a
    "programs always open" section holding Google Summer of Code and Outreachy
    -- programmes, not postings you apply to for a summer internship."""

    README = """
## the list

| Company | Role | Location | Apply |
| --- | --- | --- | --- |
| Acme | Software Engineer Intern | NYC | [Apply](https://job-boards.greenhouse.io/acme/jobs/1) |

## programs always open

| org | opportunity | type |
| --- | --- | --- |
| Google | [Summer of Code](https://summerofcode.withgoogle.com/) | open-source |
| Outreachy | [Outreachy](https://www.outreachy.org/) | open-source |

## programs open now

| org | opportunity | type | deadline |
| --- | --- | --- | --- |
| Apple | [Software Undergrad Engineering Internship](https://jobs.apple.com/en-us/details/200664785-3810/x) | SWE intern | rolling |
| MLH | [MLH Fellowship](https://fellowship.mlh.io/) | fellowship | rolling |
"""
    SKIP = [r"^programs always open$"]

    def test_the_programmes_table_is_skipped(self):
        jobs = parse_markdown(self.README, "sndsh404", self.SKIP)
        titles = [j.title for j in jobs]
        assert "Summer of Code" not in titles
        assert "Outreachy" not in titles

    def test_the_real_list_is_untouched(self):
        jobs = parse_markdown(self.README, "sndsh404", self.SKIP)
        assert "Software Engineer Intern" in [j.title for j in jobs]

    def test_a_mixed_section_is_not_skipped(self):
        """"programs open now" holds three genuine Apple internships alongside
        the junk, so skipping the whole section would lose them. Filtering
        inside it is relevance.py's job."""
        jobs = parse_markdown(self.README, "sndsh404", self.SKIP)
        titles = [j.title for j in jobs]
        assert "Software Undergrad Engineering Internship" in titles
        assert "MLH Fellowship" in titles      # dropped later, by relevance

    def test_no_skip_list_preserves_the_old_behaviour_exactly(self):
        assert (len(parse_markdown(self.README, "s", []))
                == len(parse_markdown(self.README, "s")) == 5)

    def test_a_later_heading_ends_the_skip(self):
        jobs = parse_markdown(self.README, "sndsh404", self.SKIP)
        assert any(j.company == "Apple" for j in jobs)

    def test_patterns_are_matched_case_insensitively(self):
        readme = self.README.replace("## programs always open", "## Programs Always Open")
        jobs = parse_markdown(readme, "sndsh404", self.SKIP)
        assert "Summer of Code" not in [j.title for j in jobs]
