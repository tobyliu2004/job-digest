"""Tests for noise filtering.

The risk here runs both ways. Too loose and the digest fills with campus
student-worker jobs; too tight and it silently drops the postings it exists to
catch. These pin both edges, using titles taken from live source data.
"""

from src.models import Job
from src.relevance import filter_jobs, is_obvious_noise, is_relevant


def job(company, title, unconfirmed=False, indirect=False):
    return Job(company=company, title=title, apply_url=f"https://x/{title}",
               source="test", unconfirmed=unconfirmed, indirect=indirect)


class TestConfirmedPostingsAreNeverFiltered:
    """The main list already passed a category filter at the source. Touching
    it would be a regression against a digest that was working well."""

    def test_a_tagged_posting_from_an_unknown_company_is_kept(self):
        kept, _, _ = filter_jobs([job("Montenson", "Software Engineer Intern")])
        assert len(kept) == 1

    def test_a_tagged_posting_with_an_odd_title_is_kept(self):
        kept, _, _ = filter_jobs([job("Etched", "Summer Analyst")])
        assert len(kept) == 1

    def test_only_flagrant_junk_is_dropped_from_tagged_postings(self):
        kept, noise, _ = filter_jobs([job("Aramark", "Student Barista")])
        assert kept == [] and noise == 1


class TestUntaggedBucketKeepsNotableEmployers:
    """An untagged posting can't be confirmed as a 2027 role, so it earns its
    place by employer. These are the ones worth never missing."""

    def test_anthropic_fellows_program_survives(self):
        """Real posting that a keyword-only filter drops: no "engineer",
        no "intern", no "software" anywhere in the title."""
        j = job("Anthropic", "Anthropic Fellows Program - Reinforcement Learning",
                unconfirmed=True)
        assert is_relevant(j)

    def test_jane_street_operations_engineer_survives(self):
        assert is_relevant(job("Jane Street", "Trading Desk Operations Engineer",
                               unconfirmed=True))

    def test_zoox_campus_sounding_title_survives(self):
        """"Student Worker" matches the campus-job pattern, but the title also
        names real engineering work."""
        j = job("Zoox", "Student Worker - Manufacturing Software Engineer",
                unconfirmed=True)
        assert not is_obvious_noise(j)
        assert is_relevant(j)

    def test_bytedance_student_researcher_survives(self):
        assert is_relevant(job("ByteDance", "Student Researcher - LLM Post Training",
                               unconfirmed=True))

    def test_ai_lab_resident_titles_survive(self):
        assert is_relevant(job("Elicit", "Machine Learning Research Resident",
                               unconfirmed=True))
        assert is_relevant(job("Prime Intellect", "AI Research Resident - Open Source AGI",
                               unconfirmed=True))

    def test_company_suffixes_do_not_defeat_matching(self):
        assert is_relevant(job("ByteDance Inc.", "Software Engineer Intern",
                               unconfirmed=True))
        assert is_relevant(job("Jane Street Capital", "Software Engineer Intern",
                               unconfirmed=True))


class TestUntaggedBucketDropsNoise:
    def test_campus_student_jobs_are_dropped(self):
        for company, title in [
            ("Ohio State University", "Student Assistant"),
            ("PennState University", "Maps and Geospatial Assistant"),
            ("University of Texas at Austin", "Content Management Automation Student Technician"),
            ("SDSU Research Foundation", "Undergraduate Student - Port Contamination Project"),
        ]:
            assert not is_relevant(job(company, title, unconfirmed=True)), title

    def test_untagged_unknown_company_is_dropped_even_with_a_tech_title(self):
        """This is the rule that keeps the digest from filling with no-name
        postings that were never confirmed to be 2027 roles."""
        assert not is_relevant(job("Brevium", "Data Analyst Assistant", unconfirmed=True))
        assert not is_relevant(job("SharkNinja", "Codeshark", unconfirmed=True))


class TestLinkedInNeedsItsOwnCategoryGate:
    """Every other source filters by category before we see a posting.
    LinkedIn's guest search matches loosely, so broadening the queries to stop
    missing software roles also pulled in finance internships."""

    def test_finance_roles_are_dropped(self):
        for company, title in [
            ("Milliman", "Actuarial Intern"),
            ("Principal Financial Group", "Actuarial Internship (Summer 2027)"),
            ("Truist", "2027 Truist Securities - Equity Research"),
            ("Knowhere", "Private Equity/Real Estate Internship"),
            ("Nationwide", "Summer 2027 Investments Internship"),
            ("Denali Therapeutics", "Intern, Biometrics"),
            ("Metropolitan Transportation", "Pension Finance & Data Integrity Intern"),
        ]:
            j = job(company, title, indirect=True)
            kept, noise, _ = filter_jobs([j])
            assert kept == [] and noise == 1, title

    def test_real_software_roles_survive(self):
        for company, title in [
            ("ByteDance", "Software Engineer Intern (AML-Engine-Orchestration)"),
            ("IBM", "Federal Developer Intern 2027"),
            ("BUILT Biotechnologies", "Software Engineer, Internal Platforms"),
            ("MeeBoss", "Full Stack Software Engineer Intern"),
            ("Morningstar", "Internship Program - Quantitative Research"),
            ("UCI-OC Alliance", "2027 Quantitative Developer Intern"),
            ("Sargent & Lundy", "AI & Automation Intern (Summer 2027)"),
        ]:
            kept, _, _ = filter_jobs([job(company, title, indirect=True)])
            assert len(kept) == 1, title

    def test_vague_technology_titles_do_not_pass_on_linkedin(self):
        """"Technology"/"technical" appear in plenty of finance internships,
        so they are not sufficient on a source with no category filter."""
        kept, _, _ = filter_jobs([job("Some Bank", "Technology Analyst Intern",
                                      indirect=True)])
        assert kept == []

    def test_the_gate_applies_only_to_linkedin(self):
        """Simplify and the GitHub lists are already category-filtered;
        applying this to them could drop a real role for a stray word."""
        kept, _, _ = filter_jobs([job("Marshall Wace", "Technology Intern")])
        assert len(kept) == 1


class TestFilterAccounting:
    def test_counts_separate_junk_from_untagged_drops(self):
        jobs = [
            job("Google", "Software Engineer Intern"),                       # keep
            job("Anthropic", "Fellows Program - RL", unconfirmed=True),      # keep
            job("Nowhere Inc", "Software Engineer Intern", unconfirmed=True),  # untagged drop
            job("Aramark", "Student Barista"),                               # noise drop
        ]
        kept, noise, untagged = filter_jobs(jobs)
        assert len(kept) == 2
        assert noise == 1
        assert untagged == 1

    def test_filter_is_order_preserving(self):
        jobs = [job("Google", f"Software Engineer Intern {i}") for i in range(5)]
        kept, _, _ = filter_jobs(jobs)
        assert [j.title for j in kept] == [j.title for j in jobs]
