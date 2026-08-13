"""Tests for the Pitt CSC feed source and the UUID join that keeps it from
double-listing everything Simplify already reports.
"""

from src.canonical import keys_for, simplify_uuid_key
from src.locations import is_us
from src import dedupe
from src.models import Job, SourceResult
from src.scrapers.simplify_repo import _wanted_season, fetch


class FakeSession:
    """Serves one canned feed payload; fetch() only ever does a single GET."""

    def __init__(self, payload):
        self.payload = payload

    def request(self, method, url, **kw):
        class R:
            status_code = 200
            text = ""
            def json(_self):
                return self.payload
        return R()


def record(**kw):
    base = {
        "active": True, "is_visible": True, "category": "Software",
        "terms": ["Summer 2027"], "locations": ["NYC"], "url": "https://x/1",
        "company_name": "DV Trading", "title": "Software Engineer Intern",
        "id": "uuid-1", "date_posted": 1786000000,
    }
    base.update(kw)
    return base


CFG = {
    "name": "SimplifyJobs", "us_only": True, "years": ["2027", "2028"],
    "categories": ["Software", "Software Engineering", "AI/ML/Data",
                   "Data Science, AI & Machine Learning", "Quant"],
}


def fetch_one(**kw):
    return fetch(FakeSession([record(**kw)]), CFG)


class TestFeedFiltering:
    def test_a_normal_posting_is_kept(self):
        jobs = fetch_one()
        assert len(jobs) == 1
        assert jobs[0].company == "DV Trading"
        assert jobs[0].source == "SimplifyJobs"

    def test_closed_postings_are_dropped(self):
        assert fetch_one(active=False) == []

    def test_hidden_postings_are_dropped(self):
        assert fetch_one(is_visible=False) == []

    def test_hardware_and_product_are_excluded(self):
        assert fetch_one(category="Hardware") == []
        assert fetch_one(category="Product") == []
        assert fetch_one(category="Hardware Engineering") == []
        assert fetch_one(category="Product Management") == []

    def test_the_four_wanted_categories_are_included(self):
        for cat in ("Software", "Software Engineering", "AI/ML/Data",
                    "Data Science, AI & Machine Learning", "Quant"):
            assert len(fetch_one(category=cat)) == 1, cat

    def test_off_year_seasons_are_dropped(self):
        assert fetch_one(terms=["Summer 2026"]) == []
        assert fetch_one(terms=["Fall 2026"]) == []

    def test_untagged_season_is_dropped(self):
        assert fetch_one(terms=["N/A"]) == []
        assert fetch_one(terms=[]) == []

    def test_2028_is_kept(self):
        assert len(fetch_one(terms=["Winter 2028"])) == 1

    def test_foreign_only_postings_are_dropped(self):
        assert fetch_one(locations=["London, UK"]) == []
        assert fetch_one(locations=["Toronto, ON, Canada"]) == []

    def test_a_us_site_keeps_a_multi_country_posting(self):
        assert len(fetch_one(locations=["London, UK", "New York, NY"])) == 1

    def test_the_posting_uuid_is_carried(self):
        assert fetch_one()[0].extra["feed_id"] == "uuid-1"

    def test_date_posted_becomes_a_readable_date(self):
        assert fetch_one()[0].posted != ""

    def test_incomplete_records_are_skipped(self):
        assert fetch_one(url="") == []
        assert fetch_one(company_name="") == []

    def test_a_non_list_payload_raises(self):
        import pytest
        with pytest.raises(ValueError):
            fetch(FakeSession({"not": "a list"}), CFG)


class TestSeasonHelper:
    def test_matching_year(self):
        assert _wanted_season(["Summer 2027"], ["2027"])

    def test_non_matching_year(self):
        assert not _wanted_season(["Summer 2026"], ["2027"])

    def test_na_is_not_a_match(self):
        assert not _wanted_season(["N/A"], ["2027"])


class TestUuidJoin:
    """Without this, adding the feed listed 35 postings twice -- once as a
    Simplify click stub and once as a direct employer link."""

    UUID = "de926b0a-99e7-4dbd-94cd-334ec5600000"

    def _pair(self):
        typesense = Job(
            company="Citadel", title="Sector Data Scientist Intern",
            apply_url=f"https://simplify.jobs/jobs/click/{self.UUID}",
            source="Simplify", location="New York, NY, USA",
            extra={"simplify_id": self.UUID},
        )
        feed = Job(
            company="Citadel", title="Sector Data Scientist Intern",
            apply_url="https://www.citadel.com/careers/details/sector-data-scientist",
            source="SimplifyJobs", location="NYC",
            extra={"feed_id": self.UUID},
        )
        return typesense, feed

    def test_nothing_else_can_join_them(self):
        """The URLs are unrelated and the locations are written differently,
        so without the UUID both tiers miss."""
        from src.canonical import canonical_url_key, identity_key
        t, f = self._pair()
        assert canonical_url_key(t.apply_url) != canonical_url_key(f.apply_url)
        assert (identity_key(t.company, t.title, t.location)
                != identity_key(f.company, f.title, f.location))

    def test_they_share_a_uuid_key(self):
        t, f = self._pair()
        assert simplify_uuid_key(t) == simplify_uuid_key(f) == f"sj:{self.UUID}"
        assert set(keys_for(t)) & set(keys_for(f))

    def test_dedupe_collapses_them_to_one(self):
        t, f = self._pair()
        clusters, collapsed, _ = dedupe.cluster([
            SourceResult("SimplifyJobs", [f]),
            SourceResult("Simplify", [t]),
        ])
        assert len(clusters) == 1
        assert collapsed == 1

    def test_the_direct_employer_link_is_the_one_kept(self):
        """Feed-first ordering in collect() means the real ATS URL wins over
        the click stub, which also saves a redirect request."""
        t, f = self._pair()
        clusters, _, _ = dedupe.cluster([
            SourceResult("SimplifyJobs", [f]),
            SourceResult("Simplify", [t]),
        ])
        assert "citadel.com" in clusters[0].job.apply_url

    def test_a_job_without_a_uuid_is_unaffected(self):
        j = Job("X", "SWE Intern", "https://job-boards.greenhouse.io/x/jobs/1", "repo")
        assert simplify_uuid_key(j) == ""
        assert keys_for(j)


class TestUsHelper:
    def test_untagged_location_is_kept(self):
        assert is_us([])

    def test_us_cities_pass(self):
        assert is_us(["NYC"]) and is_us(["Remote in USA"])

    def test_foreign_fails(self):
        assert not is_us(["London, UK"])
