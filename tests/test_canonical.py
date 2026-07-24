"""Tests for canonical identity and deduplication.

These target the failure modes that would silently degrade the digest --
either sending the same job twice, or hiding a genuinely new one.
"""

import pytest

from src.canonical import (
    canonical_url_key,
    identity_key,
    keys_for,
    normalize_company,
    normalize_title,
)
from src.models import Job


class TestGoogleSlugSuffix:
    """The exact cross-source collision measured on live data."""

    def test_google_slug_suffix_collapses(self):
        speedyapply = "https://www.google.com/about/careers/applications/jobs/results/85564713261245126"
        sndsh = "https://www.google.com/about/careers/applications/jobs/results/85564713261245126-software-engineering-intern/"
        assert canonical_url_key(speedyapply) == canonical_url_key(sndsh)
        assert canonical_url_key(speedyapply) == "goog:85564713261245126"

    def test_distinct_google_jobs_stay_distinct(self):
        a = "https://www.google.com/about/careers/applications/jobs/results/85564713261245126"
        b = "https://www.google.com/about/careers/applications/jobs/results/95141459539174086"
        assert canonical_url_key(a) != canonical_url_key(b)


class TestATSPatterns:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://job-boards.greenhouse.io/appian/jobs/8041237?gh_src=Simplify", "gh:appian:8041237"),
            ("https://boards.greenhouse.io/appian/jobs/8041237", "gh:appian:8041237"),
            (
                "https://jobs.lever.co/palantir/f221738b-e97c-4ce3-a12a-17ada2b855e4?utm_source=github",
                "lv:palantir:f221738b-e97c-4ce3-a12a-17ada2b855e4",
            ),
            (
                "https://jobs.ashbyhq.com/quadrillion-labs/a4acc44c-31ce-41a0-ab44-2500487b4d05/application?embed=true",
                "ab:quadrillion-labs:a4acc44c-31ce-41a0-ab44-2500487b4d05",
            ),
            ("https://jobs.apple.com/en-us/details/200664323-3810", "appl:200664323"),
            ("https://www.metacareers.com/jobs/2160167211413098", "meta:2160167211413098"),
            ("https://www.amazon.jobs/jobs/10481932/apply", "amzn:10481932"),
            ("https://lifeattiktok.com/search/7654431844394322229", "tt:7654431844394322229"),
            (
                "https://www.linkedin.com/jobs/view/software-engineer-at-chaos-industries-4444729829",
                "li:4444729829",
            ),
        ],
    )
    def test_extracts_stable_id(self, url, expected):
        assert canonical_url_key(url) == expected

    def test_workday_reqid_survives_host_shard_difference(self):
        """The wd1..wd103 shard and locale path vary; the requisition id does not."""
        a = "https://nvidia.wd5.myworkdayjobs.com/en-US/nvidiaexternalcareersite/job/US-MO-St-Louis/Performance-Engineer-Intern_JR2015779"
        b = "https://nvidia.wd1.myworkdayjobs.com/nvidiaexternalcareersite/job/Remote/Performance-Engineer-Intern_JR2015779"
        assert canonical_url_key(a) == canonical_url_key(b) == "wd:jr2015779"

    def test_tracking_params_do_not_split_identity(self):
        base = "https://job-boards.greenhouse.io/transmarketgroup/jobs/5151577007"
        tagged = base + "?utm_source=github-vansh-ouckah&gh_src=abc"
        assert canonical_url_key(base) == canonical_url_key(tagged)

    def test_unknown_host_falls_back_to_normalised_url(self):
        a = "https://careers.example.com/jobs/123?utm_source=x"
        b = "https://www.careers.example.com/jobs/123/"
        assert canonical_url_key(a) == canonical_url_key(b) == "url:careers.example.com/jobs/123"


class TestNormalisation:
    def test_company_suffixes_stripped(self):
        assert normalize_company("Caterpillar Inc.") == normalize_company("Caterpillar")
        assert normalize_company("Palantir Technologies") == normalize_company("Palantir")

    def test_title_season_and_degree_noise_stripped(self):
        a = normalize_title("Software Engineering Intern - BS - Summer 2027")
        b = normalize_title("Software Engineering Intern, Summer 2027")
        assert a == b == "software engineering"

    def test_genuinely_different_roles_stay_distinct(self):
        assert normalize_title("Backend Engineer Intern") != normalize_title("Frontend Engineer Intern")


class TestTwoTierDedup:
    def test_same_role_two_ats_systems_collapses_via_tier2(self):
        """Tier 1 cannot see this; tier 2 must."""
        a = Job("Stripe", "Software Engineer Intern", "https://job-boards.greenhouse.io/stripe/jobs/111", "repo-a", "New York, NY, USA")
        b = Job("Stripe", "Software Engineer Intern", "https://jobs.lever.co/stripe/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "repo-b", "New York, NY")
        assert canonical_url_key(a.apply_url) != canonical_url_key(b.apply_url)
        assert set(keys_for(a)) & set(keys_for(b))

    def test_location_variants_still_match(self):
        assert identity_key("Stripe", "SWE Intern", "New York, NY, USA") == identity_key(
            "Stripe", "SWE Intern", "New York, NY"
        )

    def test_different_companies_do_not_collapse(self):
        a = Job("Stripe", "Software Engineer Intern", "https://x.com/1", "a", "NYC")
        b = Job("Square", "Software Engineer Intern", "https://x.com/2", "a", "NYC")
        assert not (set(keys_for(a)) & set(keys_for(b)))

    def test_empty_company_yields_no_identity_key(self):
        """Must not collapse unrelated jobs that both lack a company."""
        assert identity_key("", "Some Role", "NYC") == ""
