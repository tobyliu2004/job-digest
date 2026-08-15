"""Junk filtering, and -- more importantly -- what it must NOT filter.

Every "must keep" case below is a real posting scraped on 2026-08-13. They are
the guard against the failure the filter could easily cause: quietly deleting a
job worth applying to. If a future rule change breaks one of these, that is the
rule's fault, not the test's.
"""

from __future__ import annotations

import pytest
import yaml

from src import relevance
from src.main import CONFIG_PATH
from src.models import Job


def job(title, company="Acme", url="https://job-boards.greenhouse.io/acme/jobs/1"):
    return Job(company=company, title=title, apply_url=url, source="repo")


@pytest.fixture(scope="module")
def judge():
    """The REAL shipped rules, not a test-only copy."""
    with open(CONFIG_PATH) as fh:
        return relevance.load(yaml.safe_load(fh))


# Real postings that must survive. Several would be lost by an obvious
# tech-keyword allowlist, and several more by an obvious off-domain blocklist.
MUST_KEEP = [
    "Infrastructure Intern",                                    # Etched
    "Video Algorithms Intern, Video Coding (Gaussian Splatting)",  # Netflix
    "Gameplay Programmer Intern",                               # Epic Games
    "Software Engineer Intern",
    "Embedded Software Development Intern - UWB",               # NXP
    "Software Quality Assurance (SQA) Engineer I",
    "Research Scientist Intern, Trust and Safety",
    "Machine Learning Research Intern - Summer 2027",           # IMC
    "Quantitative Trader Intern",                               # Tower Research
    "Quantitative Developer Intern",
    "Full-Stack Software Engineer Internship (Summer 2027)",
    "Cybersecurity Software Intern Engineer",                   # General Dynamics
    "Data Science Engineer Internship",                         # CCC
    "Backend Engineer Intern (Agentic AI)",                     # Autter
    "Self-Built Engineer Intern (CDN Platform) - 2027 Summer",  # ByteDance
    "Privacy and Civil Liberties Software Engineer Intern",     # Palantir
    "Supply Chain Data Analyst Intern",                         # Motorola: data role
    "Mechanical Systems Software Engineer Intern",              # allowlist beats maybe
    "Software Development Intern",
    "AI Engineer Intern",
    "Web Development Intern",
    "UIUC Research Park Intern - ML AI Motor Controls Algorithm",  # ML wins

    # Software that lives next to hardware. The 2026-08-14 hardware rules must
    # not touch these -- every one is a real posting, and most are the exact
    # ones the SimplifyJobs category denylist (b26efa6) was written to recover.
    "Software Engineer Intern - Summer 2027",                    # RTX, filed under "Hardware"
    "Flight Software Intern",                                    # Varda
    "Embedded Software Co-op",                                   # Specter
    "Firmware Engineer Intern",                                  # Tesla
    "Embedded Systems Engineering Intern",                       # GE Aerospace
    "Software Engineer Intern, Chip Design Tools",               # Atomic Semi
    "Applied AI Engineer Intern - AI Hardware",                  # Tesla
    "Software / Hardware Engineering Intern",                    # Nightwing
    "Electrical Engineer Intern - Implant Embedded Systems",     # Neuralink
    "Software Developer - Manufacturing Engineering",            # General Motors
    "Aerospace Software Apps Engineer Intern",                   # Blue Origin
    "Computer Science Intern - Advanced Structures and Materials",  # Gulfstream
    "Robotics Software Intern",
    "Autonomy Engineer Intern",

    # The off-domain word lists are matched as prefixes in places, so these
    # guard the other direction: a domain word plus software vocabulary is
    # still a software job.
    "Geospatial Software Engineer Intern",
    "Seismic Data Processing Software Intern",
    "Software Engineer Intern - Radar Systems",
    "Data Engineer Intern - Mining Analytics",

    # Software roles that happen to sit next to a demoted business word. Each
    # is a real posting the product/operations rules would otherwise have taken.
    "Fullstack Engineer Intern - Product Team",                 # Composio
    "Systems Engineer / Product Manager Intern (Summer 2027)",  # Dedalus Labs
    "Co-Op - Software Product Management",                      # Lila Sciences
    "Data Operations Intern",                                   # UHY
    "Technology and Operations Intern Conversion - Data",       # Truist Bank
    "Future Intern, Early Interest (Summer 2027, Software / AI/ML)",  # CloudFit
]

# Not applicable, or not a job posting at all.
MUST_DROP = [
    "General Interest - Internship",                            # Cosm
    "DoD Skillbridge Internship for Transitioning Military Service Members",
    "US Military Skillbridge Internship - Software Engineer",
    "Summer of Code",                                           # Google
    "Outreachy",
    "MLH Fellowship",
    "Neo Scholars",
    "Learn Student Ambassadors",                                # Microsoft
    "2027 Summer Internships, Express Your Interest",           # Schonfeld
    "Join our Talent Community",
]

# Real internships, wrong field. Demoted, never deleted.
MUST_BE_MAYBE = [
    "Tax Technology Intern - Summer 2027",                      # Grant Thornton
    "IT Audit Intern (Summer 2027)",                            # Kearney
    "Geology & Geophysics Intern",                              # ConocoPhillips
    "Geoscience Intern - Geoscientist",                         # BP, 2026-08-14 digest
    "Geosciences Intern",                                       # ConocoPhillips
    "Radar Digital Products Engineering Intern",                # RTX, 2026-08-14 digest
    "Marketing Intern",
    "Human Resources Intern",
    "Global Markets Sales & Trading Summer Analyst",            # RBC
    "AI Engineer (Unpaid Internship)",
    "Equity Options Co-op with Drexel University",
    "Nursing Intern",
    "Paralegal Intern",
    "Product Management Intern",                                # American Express
    "Associate Product Manager Intern (APM)",                   # Salesforce
    "Operations Finance Rotational Program Intern",             # Amazon
    "Operations Management Intern",                             # Cargill
    "Summer Analyst, Corporate Functions Operations",           # BNP Paribas
    "Summer Internship Program, AMERS (all tracks)",            # BlackRock
    "2027 US Summer Internship, Early Interest",                # Dexcom
]

# Hardware. Every one is a real posting from the 2026-08-14 digest, which led
# with nine of them. Demoted rather than dropped: Simplify files genuine
# software jobs under its "Hardware" category, so these rows stay visible in the
# Maybe section, tagged with the pattern that caught them.
MUST_BE_MAYBE_HARDWARE = [
    "ASIC Design Engineer Intern - Video Silicon IP",            # ByteDance
    "FPGA Engineer Intern",                                      # Optiver, HPR, Virtu
    "Hardware Engineer Intern - FPGA",                           # Virtu Financial
    "Hardware Engineer (FPGA/ASIC) Intern",                      # Jane Street
    "Silicon Engineer Intern/Co-op",                             # SpaceX
    "Hardware Undergrad Engineering Internship",                 # Apple
    "Hardware Engineering Intern",                               # Western Digital
    "Electronics Design Engineer Intern",                        # Heron Power
    "Electronic Design Engineer Intern - Electronic Systems",     # Tesla
    "Electrical Engineering Intern (Summer 2027)",               # Second Order Effects
    "Thermal Engineer Intern - Summer 2027",                     # Vertiv
    "Analog IC Design Co-op",                                    # Skyworks
    "RF Engineer Intern",
]


@pytest.mark.parametrize("title", MUST_KEEP)
def test_real_jobs_are_never_filtered(title, judge):
    verdict = judge.judge(job(title))
    assert verdict.action == relevance.KEEP, (
        f"{title!r} was {verdict.action} by {verdict.rule} {verdict.pattern!r}"
    )


@pytest.mark.parametrize("title", MUST_DROP)
def test_non_jobs_are_dropped(title, judge):
    assert judge.judge(job(title)).action == relevance.DROP, title


@pytest.mark.parametrize("title", MUST_BE_MAYBE)
def test_off_domain_is_demoted_not_deleted(title, judge):
    assert judge.judge(job(title)).action == relevance.MAYBE, title


@pytest.mark.parametrize("title", MUST_BE_MAYBE_HARDWARE)
def test_hardware_is_demoted(title, judge):
    verdict = judge.judge(job(title))
    assert verdict.action == relevance.MAYBE, (
        f"{title!r} stayed in the main list. Check allow_titles -- one over-broad "
        f"word there disables the hardware rules entirely."
    )


def test_allow_titles_holds_no_word_that_rescues_all_hardware(judge):
    """The 2026-08-14 regression, as an assertion.

    allow_titles once held bare 'engineer' and the words hardware/silicon/asic/
    fpga. Any one of them rescues every hardware title, which silently turns the
    hardware rules below into dead config -- the failure is invisible because
    nothing errors, the digest just fills with ASIC roles again.
    """
    for title in MUST_BE_MAYBE_HARDWARE:
        rescued = relevance._first(judge._allow, title)
        assert not rescued, f"allow_titles pattern {rescued!r} rescues {title!r}"


class TestAggregatorSpam:
    @pytest.mark.parametrize("company", ["Genusjob", "MeeBoss", "Jobright.ai"])
    def test_reposters_are_dropped(self, company, judge):
        assert judge.judge(job("Software Engineer Intern", company=company)).action \
            == relevance.DROP

    @pytest.mark.parametrize("url", [
        "https://summerofcode.withgoogle.com/",
        "https://www.outreachy.org/",
        "https://fellowship.mlh.io/",
        "https://www.databricks.com/university/student-fellows",
        "https://mvp.microsoft.com/studentambassadors",
        "https://neo.com/scholars",
    ])
    def test_programme_urls_are_dropped(self, url, judge):
        assert judge.judge(job("Engineering Programme", url=url)).action == relevance.DROP


class TestPrecedence:
    def test_a_block_beats_the_allowlist(self, judge):
        """Blocks answer 'is this a job at all', which software vocabulary in
        the title cannot change."""
        verdict = judge.judge(job("Summer of Code Software Engineer"))
        assert verdict.action == relevance.DROP

    def test_the_allowlist_beats_the_maybe_list(self, judge):
        verdict = judge.judge(job("Mechanical Systems Software Engineer Intern"))
        assert verdict.action == relevance.KEEP
        assert verdict.rescued
        assert verdict.rule == "allow_titles"

    def test_a_rescue_reports_the_pattern_that_saved_it(self, judge):
        verdict = judge.judge(job("Supply Chain Data Analyst Intern"))
        assert verdict.rescued and verdict.pattern

    def test_a_demotion_reports_the_pattern_that_fired(self, judge):
        verdict = judge.judge(job("Tax Technology Intern"))
        assert verdict.rule == "maybe_titles"
        assert "tax" in verdict.pattern


class TestModes:
    def _judge(self, **over):
        return relevance.Relevance({
            "enabled": True, "mode": "maybe",
            "maybe_titles": [r"\btax\b"], "block_titles": [r"\bskillbridge\b"],
            "allow_titles": [r"\bsoftware\b"], **over,
        })

    def test_disabled_keeps_everything(self):
        judge = self._judge(enabled=False)
        assert judge.judge(job("Skillbridge Tax Intern")).action == relevance.KEEP

    def test_mode_off_keeps_everything(self):
        judge = self._judge(mode="off")
        assert judge.judge(job("Skillbridge Tax Intern")).action == relevance.KEEP

    def test_mode_drop_hard_drops_the_borderline(self):
        assert self._judge(mode="drop").judge(job("Tax Intern")).action == relevance.DROP

    def test_mode_maybe_demotes_the_same_job(self):
        assert self._judge().judge(job("Tax Intern")).action == relevance.MAYBE


class TestTagging:
    def test_tag_annotates_and_keeps(self, judge):
        j = job("Tax Technology Intern")
        assert judge.tag(j) is True                # not dropped
        assert j.relevance == relevance.MAYBE
        assert j.relevance_rule == "maybe_titles"

    def test_tag_rejects_a_hard_drop(self, judge):
        assert judge.tag(job("Summer of Code")) is False

    def test_kept_jobs_carry_no_rule(self, judge):
        j = job("Software Engineer Intern")
        judge.tag(j)
        assert j.relevance == relevance.KEEP and j.relevance_rule == ""


class TestConfigValidation:
    def test_a_bad_regex_fails_loudly_at_load(self):
        """A pattern that silently never matches looks like a working filter."""
        with pytest.raises(ValueError, match="bad regex"):
            relevance.Relevance({"enabled": True, "maybe_titles": ["(unclosed"]})

    def test_the_shipped_config_compiles(self, judge):
        assert judge.enabled
        assert judge.mode in {"maybe", "drop", "off"}


class TestAgainstTheLiveCorpus:
    """Bounds on the real 1,407-job scrape. A rule change that starts eating
    the digest fails here rather than in your inbox."""

    def _verdicts(self, judge, corpus_jobs):
        return [judge.judge(j) for j in corpus_jobs]

    def test_drop_rate_is_tiny(self, judge, corpus_jobs):
        drops = sum(1 for v in self._verdicts(judge, corpus_jobs)
                    if v.action == relevance.DROP)
        assert drops <= 0.03 * len(corpus_jobs), f"{drops}/{len(corpus_jobs)} dropped"

    def test_maybe_rate_is_small(self, judge, corpus_jobs):
        maybes = sum(1 for v in self._verdicts(judge, corpus_jobs)
                     if v.action == relevance.MAYBE)
        assert maybes <= 0.10 * len(corpus_jobs), f"{maybes}/{len(corpus_jobs)} demoted"

    def test_the_vast_majority_is_untouched(self, judge, corpus_jobs):
        """Of the three rate guards, this is the one with slack left.

        After the 2026-08-14 rules (hardware, earth sciences, product
        management, operations) the corpus sits at 95.8% keep (1375/1436)
        against a 95% floor -- about 11 postings of slack, so the next widening
        is likely to trip this. Drops are unchanged and the maybe rate is 3.6%
        against a 10% ceiling, so this is the binding guard.

        When it does trip, re-measure with --audit-filter and read the MAYBE
        section row by row. Lowering 0.95 to make it green is the one response
        that is always wrong: the number exists to force that read.
        """
        keeps = sum(1 for v in self._verdicts(judge, corpus_jobs)
                    if v.action == relevance.KEEP)
        assert keeps >= 0.95 * len(corpus_jobs)

    def test_no_software_engineer_intern_is_ever_dropped(self, judge, corpus_jobs):
        """The single most important negative: the digest's core role type
        must be untouchable."""
        for j in corpus_jobs:
            low = j.title.lower()
            if "software engineer intern" in low and "skillbridge" not in low:
                assert judge.judge(j).action != relevance.DROP, j.title
