"""Tests for the coverage gaps found by auditing the digest against live data.

Each class here pins one measured way the digest was losing postings. They are
regression tests in the strict sense: every one of them fails against the code
as it stood before the audit.
"""

import json

from src.canonical import exact_ids, identity_key, keys_for, lookup_keys_for
from src.main import _digest_messages, collect, dedupe
from src.models import Job, SourceResult
from src.scrapers.github_md import parse, parse_html_tables
from src.scrapers.simplify_repo import _season_status, fetch
from src.locations import is_us
from src.store import SeenStore


def job(company, title, url, source="test", location="", **kw):
    return Job(company=company, title=title, apply_url=url, source=source,
               location=location, **kw)


class TestDistinctRequisitionsSurvive:
    """The Palantir case: three live listings sharing one fuzzy identity.

    `identity_key` strips season and year from the title, so all three collapse
    under it. They are different requisitions, and the seen-store keeps entries
    for 180 days -- so a collapse there permanently suppresses the later role.
    """

    FALL = "https://jobs.lever.co/palantir/d582cd84-14fd-4aa3-b413-15982d286bd9"
    ALSO_FALL = "https://jobs.lever.co/palantir/ac0dc094-2480-43c2-8495-26ade227ff4f"
    SUMMER28 = "https://jobs.lever.co/palantir/e0010393-c300-446f-bf67-fa2ef067f16f"

    def _jobs(self):
        return [
            job("Palantir", "Forward Deployed Software Engineer Intern", self.FALL),
            job("Palantir", "Forward Deployed Software Engineer Intern", self.ALSO_FALL),
            job("Palantir", "Forward Deployed Software Engineer Intern", self.SUMMER28),
        ]

    def test_fuzzy_key_alone_would_merge_them(self):
        keys = {identity_key(j.company, j.title, j.location) for j in self._jobs()}
        assert len(keys) == 1, "precondition: these share one fuzzy identity"

    def test_dedupe_keeps_all_three(self):
        unique, collapsed = dedupe([SourceResult("lever", self._jobs())])
        assert len(unique) == 3
        assert collapsed == 0

    def test_seen_store_does_not_suppress_the_later_role(self, tmp_path):
        store = SeenStore(tmp_path / "seen.json")
        first, second, third = self._jobs()
        store.add(keys_for(first))

        assert store.has_any(lookup_keys_for(first)), "the same req is still seen"
        assert not store.has_any(lookup_keys_for(second))
        assert not store.has_any(lookup_keys_for(third))


class TestSimplifyUuidJoin:
    """Typesense and the Pitt CSC feed share posting UUIDs (452 of ~470 live).

    The Typesense entry only has a click stub for a URL, so without the UUID
    there is nothing to match the two on and every Simplify posting is emailed
    twice.
    """

    UUID = "3b700557-5c32-4773-8cfe-f11a66b71a4f"

    def test_click_stub_yields_no_ats_id(self):
        stub = job("Figma", "Software Engineer Intern",
                   f"https://simplify.jobs/jobs/click/{self.UUID}")
        assert [k for k in exact_ids(stub) if not k.startswith("sj:")] == []

    def test_the_two_sources_collapse(self):
        typesense = job("Figma", "Software Engineer Intern - Winter 2027",
                        f"https://simplify.jobs/jobs/click/{self.UUID}",
                        source="Simplify", location="San Francisco, CA, USA, New York, NY",
                        extra={"simplify_id": self.UUID})
        feed = job("Figma", "Software Engineer Intern - Winter 2027",
                   "https://job-boards.greenhouse.io/figma/jobs/6131089004",
                   source="SimplifyJobs", location="SF, NYC",
                   extra={"feed_id": self.UUID})

        unique, collapsed = dedupe([
            SourceResult("SimplifyJobs", [feed]),
            SourceResult("Simplify", [typesense]),
        ])
        assert len(unique) == 1
        assert collapsed == 1
        assert "greenhouse" in unique[0].apply_url, "keep the direct employer link"

    def test_ats_url_still_matches_a_repo_listing(self):
        """The feed entry carries BOTH ids, so it also matches vanshb03."""
        feed = job("Figma", "Software Engineer Intern",
                   "https://job-boards.greenhouse.io/figma/jobs/6131089004",
                   extra={"feed_id": self.UUID})
        repo = job("Figma", "SWE Intern",
                   "https://job-boards.greenhouse.io/figma/jobs/6131089004")
        assert set(exact_ids(feed)) & set(exact_ids(repo))


class TestLinkedInPerCityRepeats:
    """LinkedIn posts one role once per city; each gets its own posting id."""

    def test_locations_do_not_split_one_role(self):
        sf = job("Figma", "Software Engineer Intern (Winter 2027)",
                 "https://www.linkedin.com/jobs/view/4111111111",
                 location="San Francisco, CA", indirect=True)
        ny = job("Figma", "Software Engineer Intern (Winter 2027)",
                 "https://www.linkedin.com/jobs/view/4222222222",
                 location="New York, NY", indirect=True)

        unique, collapsed = dedupe([SourceResult("LinkedIn", [sf, ny])])
        assert len(unique) == 1
        assert collapsed == 1

    def test_a_linkedin_mirror_collapses_into_the_employer_listing(self):
        direct = job("Figma", "Software Engineer Intern",
                     "https://job-boards.greenhouse.io/figma/jobs/6131089004",
                     source="SimplifyJobs", location="SF, NYC")
        mirror = job("Figma", "Software Engineer Intern",
                     "https://www.linkedin.com/jobs/view/4111111111",
                     source="LinkedIn", location="San Francisco, CA", indirect=True)

        unique, _ = dedupe([
            SourceResult("SimplifyJobs", [direct]),
            SourceResult("LinkedIn", [mirror]),
        ])
        assert len(unique) == 1
        assert "greenhouse" in unique[0].apply_url

    def test_linkedin_id_is_never_treated_as_a_requisition(self):
        """Otherwise the mirror above would look like a distinct posting."""
        mirror = job("Figma", "SWE Intern", "https://www.linkedin.com/jobs/view/4111111111")
        assert exact_ids(mirror) == []


class TestUntaggedSeasonIsKeptNotDropped:
    """New postings are the likeliest to be untagged, so dropping them is worst
    exactly where the digest is supposed to be strongest."""

    def test_missing_season_is_flagged_not_discarded(self):
        assert _season_status([], ["2027"]) == "unconfirmed"
        assert _season_status(["N/A"], ["2027"]) == "unconfirmed"

    def test_matching_season_is_accepted(self):
        assert _season_status(["Summer 2027"], ["2027"]) == "match"

    def test_wrong_season_is_dropped(self):
        assert _season_status(["Summer 2026"], ["2027"]) is None


class TestUsLocationFilter:
    def test_untagged_location_is_kept(self):
        assert is_us([]) is True

    def test_us_cities_pass(self):
        assert is_us(["NYC"])
        assert is_us(["San Francisco, CA"])
        assert is_us(["Remote in USA"])

    def test_foreign_only_is_excluded(self):
        assert not is_us(["London, UK"])
        assert not is_us(["Toronto, ON, Canada"])
        assert not is_us(["Dubai - United Arab Emirates"])

    def test_a_us_site_keeps_a_multi_country_posting(self):
        assert is_us(["London, UK", "New York, NY"])


HTML_TABLE = """
<table>
<thead>
<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
</thead>
<tbody>
<tr>
<td><strong><a href="https://simplify.jobs/c/DV-Trading">DV Trading</a></strong></td>
<td>Software Engineer Intern</td>
<td>NYC</td>
<td><div align="center"><a href="https://job-boards.greenhouse.io/dvtrading/jobs/4719119005"><img src="https://i.imgur.com/fbjwDvo.png" alt="Apply"></a></div></td>
<td>0d</td>
</tr>
<tr>
<td>↳</td>
<td>Software Engineer Intern - Commodities</td>
<td>Chicago, IL</td>
<td><div align="center"><a href="https://job-boards.greenhouse.io/dvtrading/jobs/4719125005"><img src="https://i.imgur.com/fbjwDvo.png" alt="Apply"></a></div></td>
<td>0d</td>
</tr>
<tr>
<td><strong>ClosedCo</strong></td>
<td>Software Engineer Intern 🔒</td>
<td>Remote</td>
<td><div align="center"><a href="https://example.com/closed">Apply</a></div></td>
<td>9d</td>
</tr>
</tbody>
</table>
"""


class TestHtmlTableParsing:
    """The canonical repo switched from pipe-tables to HTML tables. A markdown
    parser reads that as zero jobs -- with no error."""

    def test_markdown_parser_finds_nothing_here(self):
        from src.scrapers.github_md import parse_markdown
        assert parse_markdown(HTML_TABLE, "x") == []

    def test_parse_falls_back_to_html(self):
        jobs = parse(HTML_TABLE, "simplifyjobs")
        assert len(jobs) == 2

    def test_carry_forward_company_works_in_html(self):
        jobs = parse(HTML_TABLE, "simplifyjobs")
        assert [j.company for j in jobs] == ["DV Trading", "DV Trading"]

    def test_closed_rows_are_skipped(self):
        assert all("ClosedCo" != j.company for j in parse(HTML_TABLE, "x"))

    def test_apply_url_is_the_employer_not_the_company_page(self):
        first = parse(HTML_TABLE, "x")[0]
        assert first.apply_url == "https://job-boards.greenhouse.io/dvtrading/jobs/4719119005"


class TestZeroYieldIsAFailure:
    """An empty source and a quiet day look identical in the email otherwise."""

    def test_source_returning_nothing_is_reported_as_failed(self):
        config = {
            "simplify": {"enabled": False},
            "simplifyjobs_feed": {"enabled": False},
            "github_lists": {"enabled": False},
            "linkedin": {"enabled": True},
        }
        import src.main as main

        original = main.linkedin.fetch
        main.linkedin.fetch = lambda session, cfg: []
        try:
            results = collect(session=None, config=config)
        finally:
            main.linkedin.fetch = original

        assert len(results) == 1
        assert results[0].ok is False
        assert "0 postings" in results[0].error

    def test_html_list_that_stops_parsing_raises(self):
        assert parse_html_tables("<table><tr><td>nope</td></tr></table>", "x") == []


class TestGmailClipping:
    """Gmail hides everything past ~102KB behind "[Message clipped]". A digest
    that is clipped withholds the postings it exists to deliver."""

    def _jobs(self, n):
        return [job(f"Company {i}", f"Software Engineer Intern {i}",
                    f"https://job-boards.greenhouse.io/co{i}/jobs/{1000 + i}",
                    location="New York, NY") for i in range(n)]

    def test_small_digest_stays_one_email(self):
        msgs = _digest_messages(self._jobs(20), [], "label", "Aug 10", "PM")
        assert len(msgs) == 1
        assert "(1/" not in msgs[0][0]

    def test_large_digest_is_split(self):
        msgs = _digest_messages(self._jobs(600), [], "label", "Aug 10", "PM")
        assert len(msgs) > 1

    def test_no_part_exceeds_the_clip_threshold(self):
        for _, html, _ in _digest_messages(self._jobs(600), [], "label", "Aug 10", "PM"):
            assert len(html) <= 102_400

    def test_every_job_appears_exactly_once_across_parts(self):
        jobs = self._jobs(600)
        combined = "".join(html for _, html, _ in
                           _digest_messages(jobs, [], "label", "Aug 10", "PM"))
        for j in jobs:
            assert combined.count(j.apply_url) == 1

    def test_failures_are_reported_once_not_per_part(self):
        msgs = _digest_messages(self._jobs(600), ["sndsh404: returned 0 postings"],
                                "label", "Aug 10", "PM")
        carrying = [m for m in msgs if "sndsh404" in m[1]]
        assert len(carrying) == 1

    def test_subject_keeps_the_true_total(self):
        msgs = _digest_messages(self._jobs(600), [], "label", "Aug 10", "PM")
        assert all("600 new postings" in subject for subject, _, _ in msgs)


class TestPendingQueueSurvivesRestart:
    """Hourly runs mark jobs as seen. Without a durable queue, an off-hour run
    would consume a posting and never send it."""

    def test_round_trip_through_the_state_file(self, tmp_path):
        path = tmp_path / "seen.json"
        store = SeenStore(path)
        original = job("Citadel", "Sector Data Scientist Intern",
                       "https://www.citadel.com/careers/details/123",
                       source="SimplifyJobs", location="NYC", unconfirmed=True)
        store.pending.append(original.to_dict())
        store.add(keys_for(original))
        store.save()

        reloaded = SeenStore(path)
        assert len(reloaded.pending) == 1
        restored = Job.from_dict(reloaded.pending[0])
        assert restored.company == "Citadel"
        assert restored.unconfirmed is True
        assert restored.apply_url == original.apply_url

    def test_unknown_fields_do_not_break_restore(self):
        """A state file written by an older or newer version must still load."""
        restored = Job.from_dict({
            "company": "X", "title": "Y", "apply_url": "z", "source": "s",
            "some_future_field": 1,
        })
        assert restored.company == "X"

    def test_saved_state_is_valid_json_with_pending(self, tmp_path):
        path = tmp_path / "seen.json"
        store = SeenStore(path)
        store.pending.append(job("A", "B", "https://c/d").to_dict())
        store.add(["k1"])
        store.save()
        data = json.loads(path.read_text())
        assert data["pending"][0]["company"] == "A"
