"""vanshb03 wraps every apply link in a go.vansh.nyc short link (2026-10-25).

The case below is the real one: Point72's SWE internship was sent from the
Pitt CSC feed on Oct 14, then came back on Oct 25 as a short link with no
season, matched nothing stored, and was emailed again.
"""

from src import canonical, dedupe, main
from src.models import Job, SourceResult
from src.scrapers import github_md

SHORT = "https://go.vansh.nyc/9m?s=y"
TARGET = "https://boards.greenhouse.io/point72/jobs/8885733002?gh_jid=8885733002"


def vansh_row(url=SHORT):
    return Job(company="Point72", title="Software Engineer Intern",
               apply_url=url, source="vanshb03-summer2027", location="New York, NY")


def feed_copy():
    return Job(company="Point72", title="Software Engineer Intern", apply_url=TARGET,
               source="SimplifyJobs", location="New York, NY", season="Summer 2027")


class FakeRedirects:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, session, url):
        self.calls.append(url)
        return self.table.get(url)


def test_short_link_is_swapped_for_the_employer_url(monkeypatch):
    monkeypatch.setattr(github_md, "resolve_redirect", FakeRedirects({SHORT: TARGET}))
    job = vansh_row()
    assert github_md.resolve_short_links(None, [job], {}) == 1
    assert job.apply_url == TARGET
    assert job.extra["short_url"] == SHORT


def test_a_job_already_sent_from_another_source_is_recognised(monkeypatch):
    monkeypatch.setattr(github_md, "resolve_redirect", FakeRedirects({SHORT: TARGET}))
    seen = dict.fromkeys(canonical.keys_for(feed_copy()), "2026-10-14")
    job = vansh_row()
    github_md.resolve_short_links(None, [job], seen)
    assert any(k in seen for k in canonical.keys_for(job))


def test_the_short_link_key_is_kept_alongside_the_real_one(monkeypatch):
    monkeypatch.setattr(github_md, "resolve_redirect", FakeRedirects({SHORT: TARGET}))
    job = vansh_row()
    github_md.resolve_short_links(None, [job], {})
    keys = canonical.keys_for(job)
    assert "url:go.vansh.nyc/9m?s=y" in keys
    assert "gh:point72:8885733002" in keys


def test_a_short_link_already_stored_costs_no_request(monkeypatch):
    fake = FakeRedirects({SHORT: TARGET})
    monkeypatch.setattr(github_md, "resolve_redirect", fake)
    job = vansh_row()
    github_md.resolve_short_links(None, [job], {"url:go.vansh.nyc/9m?s=y": "x"})
    assert fake.calls == []
    assert job.apply_url == SHORT


def test_an_unresolvable_link_keeps_its_short_form(monkeypatch):
    monkeypatch.setattr(github_md, "resolve_redirect", FakeRedirects({}))
    job = vansh_row()
    assert github_md.resolve_short_links(None, [job], {}) == 0
    assert job.apply_url == SHORT
    assert "short_url" not in job.extra


def test_other_links_are_never_fetched(monkeypatch):
    fake = FakeRedirects({})
    monkeypatch.setattr(github_md, "resolve_redirect", fake)
    github_md.resolve_short_links(None, [vansh_row(TARGET)], {})
    assert fake.calls == []


def test_a_recognised_repeat_has_its_short_link_recorded(monkeypatch):
    monkeypatch.setattr(github_md, "resolve_redirect", FakeRedirects({SHORT: TARGET}))
    row = vansh_row()
    github_md.resolve_short_links(None, [row], {})
    clusters, _, _ = dedupe.cluster([SourceResult("vansh", [row])])

    class Store:
        keys = {}

        def add(self, keys):
            self.keys.update(dict.fromkeys(keys, "now"))

    store = Store()
    main._remember_short_links(clusters, [], store)
    assert list(store.keys) == ["url:go.vansh.nyc/9m?s=y"]
