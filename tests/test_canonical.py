"""Tests for canonical identity and deduplication.

These target the failure modes that would silently degrade the digest --
either sending the same job twice, or hiding a genuinely new one.
"""

import pytest

from src.canonical import (
    canonical_url_key,
    employer_ats_key,
    identity_key,
    keys_for,
    level_token,
    normalize_company,
    normalize_location,
    normalize_title,
    season_token,
    strong_url_key,
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
            ("https://aapc.applytojob.com/apply/Bwioas9wbW/Junior-Salesforce-Developer", "atj:aapc:Bwioas9wbW"),
            ("https://ats.rippling.com/en-GB/spreeai/jobs/c52472cb-2671-45d7-b666-17196dc3df25", "rip:spreeai:c52472cb-2671-45d7-b666-17196dc3df25"),
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
        assert canonical_url_key(a) == canonical_url_key(b) == "wd:nvidia:jr2015779"

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


class TestTenantIsAlwaysCaptured:
    """Requisition ids are per-employer counters. Keying on the id alone let
    one company's job silently bury another's -- live state held 138 Workday
    keys with ids short enough to collide."""

    def test_workday_tenant_separates_identical_requisitions(self):
        a = canonical_url_key("https://acme.wd1.myworkdayjobs.com/careers/job/NY/Intern_JR1234")
        b = canonical_url_key("https://other.wd5.myworkdayjobs.com/en-US/x/job/SF/Analyst_JR1234")
        assert a != b
        assert a == "wd:acme:jr1234" and b == "wd:other:jr1234"

    def test_workday_shards_of_one_tenant_still_merge(self):
        a = canonical_url_key("https://nvidia.wd1.myworkdayjobs.com/a/job/x_JR2015779")
        b = canonical_url_key("https://nvidia.wd5.myworkdayjobs.com/b/job/y_JR2015779")
        assert a == b == "wd:nvidia:jr2015779"

    def test_workday_cxs_api_form(self):
        assert canonical_url_key(
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/careers/job/NY/x_JR1234"
        ).startswith("wd:acme:")

    def test_myworkdaysite_takes_the_tenant_from_the_path(self):
        """Wells Fargo's host shard carries no tenant at all."""
        assert canonical_url_key(
            "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/job/CHARLOTTE-NC/X_R-568279"
        ) == "wd:wf:r568279"

    def test_requisition_punctuation_is_normalised(self):
        a = canonical_url_key("https://acme.wd1.myworkdayjobs.com/c/job/x_JR-000-123")
        b = canonical_url_key("https://acme.wd1.myworkdayjobs.com/c/job/y_JR_000_123")
        assert a == b == "wd:acme:jr000123"

    def test_icims_tenant_separates_short_ids(self):
        a = canonical_url_key("https://careers-a.icims.com/jobs/1234/x/job")
        b = canonical_url_key("https://careers-b.icims.com/jobs/1234/y/job")
        assert a != b

    @pytest.mark.parametrize("url,expected", [
        ("https://jobs.smartrecruiters.com/Canva/6000000001291655", "sr:canva:6000000001291655"),
        ("https://jobs.jobvite.com/altamiracorps/job/oHqCAfw3", "jv:altamiracorps:oHqCAfw3"),
        ("https://apply.workable.com/tmeic-corporation-americas/j/6FDBF2FD32/apply",
         "wk:tmeic-corporation-americas:6FDBF2FD32"),
    ])
    def test_other_ats_carry_their_tenant(self, url, expected):
        assert canonical_url_key(url) == expected


class TestQueryStringIdentity:
    """The fallback threw the query string away, so every posting on a board
    that identifies jobs by parameter collapsed onto one key. Live state still
    shows the damage: url:career41.sapsf.com/career is a single entry standing
    in for every job that board has ever posted."""

    def test_jobs_identified_by_parameter_stay_distinct(self):
        keys = {canonical_url_key(f"https://career41.sapsf.com/career?job_req_id={i}")
                for i in range(4)}
        assert len(keys) == 4

    def test_tracking_params_are_still_stripped(self):
        bare = canonical_url_key("https://careers.acme.com/a/b")
        assert canonical_url_key(
            "https://careers.acme.com/a/b?utm_source=x&gh_src=y&ref=z&trk=q") == bare

    def test_presentation_params_are_stripped(self):
        bare = canonical_url_key("https://careers.acme.com/a/b")
        assert canonical_url_key(
            "https://careers.acme.com/a/b?mobile=true&needsRedirect=false") == bare

    def test_param_order_does_not_matter(self):
        assert (canonical_url_key("https://x.com/a?b=2&a=1")
                == canonical_url_key("https://x.com/a?a=1&b=2"))

    def test_gh_jid_never_reaches_the_fallback(self):
        assert canonical_url_key(
            "https://www.tower-research.com/open-positions/?gh_jid=8024128") == "gh:jid:8024128"


class TestIdentityKeyFields:
    def test_summer_and_fall_differ(self):
        assert (identity_key("Acme", "SWE Intern, Summer 2027", "NYC")
                != identity_key("Acme", "SWE Intern, Fall 2027", "NYC"))

    def test_intern_and_new_grad_differ(self):
        assert (identity_key("Acme", "Software Engineer Intern", "NYC")
                != identity_key("Acme", "Software Engineer, New Grad", "NYC"))

    def test_degree_and_punctuation_variants_still_merge(self):
        assert (identity_key("Acme", "SWE Intern - BS - Summer 2027", "NYC")
                == identity_key("Acme", "SWE Intern, Summer 2027", "NYC"))

    def test_the_season_field_supplies_what_the_title_omits(self):
        assert (identity_key("Acme", "SWE Intern", "NYC", "Summer 2027")
                == identity_key("Acme", "SWE Intern Summer 2027", "NYC"))

    @pytest.mark.parametrize("text,expected", [
        ("Summer 2027", "su2027"), ("Fall 2027", "fa2027"),
        ("Winter 2026", "wi2026"), ("Spring 2027", "sp2027"),
        ("Summer", "su"), ("", ""), ("rolling", ""),
    ])
    def test_season_token(self, text, expected):
        assert season_token(text) == expected

    @pytest.mark.parametrize("text,expected", [
        # A posting open for several seasons must be keyed to a season/year
        # pair it ACTUALLY offers. Taking the first year in the string paired
        # summer with 2026 here -- a term this job does not have.
        ("Fall 2026, Spring 2027, Summer 2027", "su2027"),
        ("Winter 2027, Spring 2028, Summer 2028", "su2028"),
        ("Winter 2027, Summer 2026, Fall 2026", "su2026"),
        ("Winter 2026, Spring 2027, Summer 2027, Fall 2027", "su2027"),
        # No summer at all: falls to the next season in priority order.
        ("Winter 2026, Spring 2027", "wi2026"),
        # Source order must not change the key, or the same job keys two ways
        # from two sources and is emailed twice.
        ("Summer 2027, Fall 2026", "su2027"),
    ])
    def test_multi_season_pairs_the_season_with_its_own_year(self, text, expected):
        assert season_token(text) == expected

    def test_a_year_before_the_season_still_counts(self):
        """'2027 Summer Intern' has no year after the season word."""
        assert season_token("2027 Summer Intern") == "su2027"
        assert season_token("Class of 2027 - Fall Co-op") == "fa2027"

    @pytest.mark.parametrize("title,expected", [
        ("Software Engineer Intern", "intern"),
        ("Software Engineer Co-op", "intern"),
        ("Software Engineer, New Grad", "newgrad"),
        ("Senior Software Engineer", "newgrad"),
        ("Software Engineer", ""),
    ])
    def test_level_token(self, title, expected):
        assert level_token(title) == expected


class TestLocationNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("Austin, TX", "austin tx"),
        ("Houston, TX", "houston tx"),
        ("Columbus, OH", "columbus oh"),
        ("Louisville, KY", "louisville ky"),
        ("Tuscaloosa, AL", "tuscaloosa al"),
    ])
    def test_the_us_substring_no_longer_eats_city_names(self, raw, expected):
        """'us' was stripped as a substring, turning Austin into 'a tin'."""
        assert normalize_location(raw) == expected

    def test_country_suffixes_are_still_removed(self):
        assert normalize_location("New York, NY, USA") == normalize_location("New York, NY")


class TestStrongVersusAggregatorKeys:
    def test_an_employer_ats_id_is_strong(self):
        assert employer_ats_key("https://job-boards.greenhouse.io/acme/jobs/1") == "gh:acme:1"

    def test_a_linkedin_id_is_not_employer_evidence(self):
        """Every LinkedIn copy of a job has one; treating it as a requisition
        id would stop LinkedIn duplicates merging with the employer link."""
        url = "https://www.linkedin.com/jobs/view/4444729829"
        assert strong_url_key(url) == "li:4444729829"
        assert employer_ats_key(url) == ""

    def test_an_unrecognised_host_is_not_strong(self):
        assert strong_url_key("https://careers.acme.com/roles/42") == ""
