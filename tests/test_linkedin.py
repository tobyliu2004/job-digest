"""LinkedIn scraping: geography, and the noise it sends back.

The guest search endpoint was called with no geo parameter at all, so results
were worldwide. It is also the noisiest source by far -- LinkedIn treats
`keywords` as a relevance hint across the whole posting, not a filter.
"""

from __future__ import annotations

import pytest

from src.scrapers import linkedin

CARD = """
<li><div class="base-card">
  <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{id}"></a>
  <h3 class="base-search-card__title">{title}</h3>
  <h4 class="base-search-card__subtitle">{company}</h4>
  <span class="job-search-card__location">{location}</span>
</div></li>
"""


def page(*cards):
    return "".join(CARD.format(**c) for c in cards)


def card(id="4444729829", title="Software Engineer Intern",
         company="Acme", location="New York, NY"):
    return dict(id=id, title=title, company=company, location=location)


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class FakeSession:
    """Records the params of every request and replays canned pages."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append(kw.get("params", {}))
        return FakeResponse(self.pages.pop(0) if self.pages else "")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(linkedin.time, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def direct_request(monkeypatch):
    """Bypass the retry/backoff wrapper so FakeSession is called directly."""
    monkeypatch.setattr(linkedin, "request",
                        lambda session, method, url, **kw: session.request(method, url, **kw))


class TestGeoParameters:
    def test_geo_id_is_sent(self):
        session = FakeSession([page(card())])
        linkedin.fetch(session, {"max_pages": 1})
        assert session.calls[0]["geoId"] == "103644278"

    def test_location_is_sent(self):
        session = FakeSession([page(card())])
        linkedin.fetch(session, {"max_pages": 1})
        assert session.calls[0]["location"] == "United States"

    def test_config_can_override_the_region(self):
        session = FakeSession([page(card())])
        linkedin.fetch(session, {"max_pages": 1, "geo_id": "101165590",
                                 "location": "United Kingdom", "us_only": False})
        assert session.calls[0]["geoId"] == "101165590"

    def test_existing_params_are_still_sent(self):
        session = FakeSession([page(card())])
        linkedin.fetch(session, {"max_pages": 1, "keywords": "swe intern",
                                 "f_E": "1", "f_TPR": "r86400"})
        params = session.calls[0]
        assert params["keywords"] == "swe intern"
        assert params["f_E"] == "1"
        assert params["f_TPR"] == "r86400"


class TestUsOnlyPostFilter:
    """Belt and braces: the guest endpoint does not always honour geoId."""

    def test_foreign_postings_are_dropped(self):
        session = FakeSession([page(
            card(id="1", location="London, England, United Kingdom"),
            card(id="2", location="Toronto, Ontario, Canada"),
            card(id="3", location="Bengaluru, Karnataka, India"),
            card(id="4", location="New York, NY"),
        )])
        jobs = linkedin.fetch(session, {"max_pages": 1})
        assert [j.location for j in jobs] == ["New York, NY"]

    def test_untagged_locations_are_kept(self):
        """is_us is a foreign-denylist, so anything it cannot place survives --
        it must never drop a US job it fails to recognise."""
        session = FakeSession([page(card(id="1", location="United States"),
                                    card(id="2", location=""))])
        jobs = linkedin.fetch(session, {"max_pages": 1})
        assert len(jobs) == 2

    def test_us_only_false_disables_it(self):
        session = FakeSession([page(card(id="1", location="London, United Kingdom"))])
        jobs = linkedin.fetch(session, {"max_pages": 1, "us_only": False})
        assert len(jobs) == 1


class TestParsingAndPagination:
    def test_jobs_are_marked_indirect(self):
        session = FakeSession([page(card())])
        jobs = linkedin.fetch(session, {"max_pages": 1})
        assert jobs[0].indirect is True
        assert jobs[0].source == "LinkedIn"

    def test_duplicate_urls_within_a_run_are_dropped(self):
        session = FakeSession([page(card(id="1"), card(id="1"), card(id="2"))])
        jobs = linkedin.fetch(session, {"max_pages": 1})
        assert len(jobs) == 2

    def test_a_repeated_page_stops_pagination(self):
        """LinkedIn repeats the last page rather than returning empty."""
        repeated = page(card(id="1"))
        session = FakeSession([repeated, repeated, repeated])
        jobs = linkedin.fetch(session, {"max_pages": 3})
        assert len(jobs) == 1
        assert len(session.calls) == 2

    def test_offset_advances_by_cards_actually_seen(self):
        session = FakeSession([page(card(id="1"), card(id="2")), page(card(id="3"))])
        linkedin.fetch(session, {"max_pages": 2})
        assert session.calls[1]["start"] == "2"


class TestFailureHandling:
    def test_rate_limit_keeps_what_we_have(self):
        class Limited(FakeSession):
            def request(self, method, url, **kw):
                self.calls.append(kw.get("params", {}))
                if len(self.calls) == 1:
                    return FakeResponse(page(card(id="1")))
                return FakeResponse("", status=429)

        session = Limited([])
        jobs = linkedin.fetch(session, {"max_pages": 3})
        assert len(jobs) == 1

    def test_a_first_page_error_raises_so_main_marks_the_source_failed(self):
        class Broken(FakeSession):
            def request(self, method, url, **kw):
                self.calls.append(kw.get("params", {}))
                return FakeResponse("", status=500)

        with pytest.raises(RuntimeError, match="500"):
            linkedin.fetch(Broken([]), {"max_pages": 2})
