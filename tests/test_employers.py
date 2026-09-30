"""The employer list: who is worth an email, and matching their names safely.

The bar is "at least as good a resume line as the Capital One SWE internship
already in hand". What these tests pin is the matching, not the taste: every
spelling the real sources use must line up with its entry, and no name may
match a different company by accident.
"""

from __future__ import annotations

import pytest

from src import employers
from src.models import Job


@pytest.fixture(scope="module")
def listed():
    return employers.load()


def job(company):
    return Job(company=company, title="Software Engineer Intern",
               apply_url="https://job-boards.greenhouse.io/x/jobs/1", source="repo")


# Every spelling below appears verbatim in tests/fixtures/live_jobs.json.
@pytest.mark.parametrize("spelling, name", [
    ("D. E. Shaw", "D. E. Shaw"),
    ("D. E. Shaw & Co.", "D. E. Shaw"),
    ("DE Shaw", "D. E. Shaw"),
    ("JPMorganChase", "JPMorgan Chase"),
    ("JP Morgan Chase", "JPMorgan Chase"),
    ("Susquehanna International Group (SIG)", "Susquehanna"),
    ("Susquehanna Investment Group", "Susquehanna"),
    ("HPR (Hyannis Port Research)", "Hyannis Port Research"),
    ("Jump Trading Group", "Jump Trading"),
    ("Palantir Technologies", "Palantir"),
    ("Amazon Web Services (AWS)", "Amazon"),
    ("Google DeepMind", "Google"),
    ("IMC", "IMC Trading"),
    ("Tower Research", "Tower Research Capital"),
    ("Old Mission", "Old Mission Capital"),
    ("Cubist Systematic Strategies", "Point72"),
    ("Rivian and Volkswagen Group Technologies", "Rivian"),
    ("Saronic Technologies", "Saronic"),
])
def test_source_spellings_match_their_entry(spelling, name, listed):
    assert listed.match(spelling) == name


@pytest.mark.parametrize("company", [
    "Applied Materials",   # not Apple
    "Epic",                # Epic Systems, not Epic Games
    "Blockhouse",          # not Block
    "Citi",                # not Citadel
    "Metaview",            # not Meta
    "Scale",               # not Scale AI
    "Jack", "Moon", "Impact",
])
def test_no_name_matches_a_different_company(company, listed):
    assert not listed.match(company)


def test_capital_one_is_not_listed(listed):
    """Another posting at the company whose offer is in hand is not news."""
    assert not listed.listed(job("Capital One"))


def test_unlisted_companies_are_dropped(listed):
    assert not listed.listed(job("Kimley-Horn"))
    assert listed.listed(job("Jane Street"))


def test_normalisation_ignores_case_punctuation_spacing_and_suffixes():
    assert employers.normalize("D. E. Shaw & Co.") == employers.normalize("de shaw")
    assert employers.normalize("Stripe, Inc.") == employers.normalize("stripe")
    assert employers.normalize("Two Sigma (NYC)") == employers.normalize("Two Sigma")


class TestConfigValidation:
    def test_a_collision_between_entries_fails_loudly(self):
        """Two entries claiming one name means one is a typo."""
        with pytest.raises(ValueError, match="both normalise"):
            employers.Employers({"employers": {"a": ["DE Shaw", "D. E. Shaw"]}})

    def test_an_alias_repeating_its_own_name_is_fine(self):
        e = employers.Employers({"employers": {"a": [{"Stripe": ["Stripe Inc"]}]}})
        assert e.match("stripe") == "Stripe"

    def test_a_malformed_entry_fails_loudly(self):
        with pytest.raises(ValueError, match="bad entry"):
            employers.Employers({"employers": {"a": [{"X": [], "Y": []}]}})

    def test_the_shipped_list_loads(self, listed):
        assert len(listed.names) > 100
