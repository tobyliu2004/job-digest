"""Tests for internship-only filtering.

Cases are taken verbatim from a real digest the user flagged as containing
new-grad and non-intern roles.
"""

import pytest

from src.main import filter_internships, is_internship
from src.models import Job


def job(source, title):
    return Job("Co", title, "https://x", source)


@pytest.mark.parametrize(
    "source,title,keep",
    [
        # Real internships — must keep
        ("Simplify", "Quantitative Developer Intern", True),
        ("Simplify", "Software Engineer Intern", True),
        ("vanshb03-summer2027", "Campus Systems Engineer Intern", True),
        ("speedyapply-intern-usa", "Embedded Software Engineer Intern - Fall 2026", True),
        ("LinkedIn", "2027 Software Engineer Intern", True),
        ("LinkedIn", "Machine Learning Engineer Intern", True),
        ("sndsh404", "Quant Trading Intern (Summer 2027)", True),
        ("Simplify", "SWE Co-op", True),
        # New-grad / entry-level — must drop
        ("speedyapply-newgrad-usa", "Software Engineer - New Grad", False),
        ("speedyapply-newgrad-usa", "Software Engineer - 2027 Graduate Program", False),
        ("speedyapply-newgrad-usa", "Algorithm Developer - 2027 Grads", False),
        ("speedyapply-newgrad-usa", "Associate Full Stack Engineer", False),
        # LinkedIn non-intern noise (its experience filter is unreliable)
        ("LinkedIn", "Staff Engineer - Highway/Traffic", False),
        ("LinkedIn", "Software Developer", False),
        ("LinkedIn", "Engineer Trainee/Assistant Engineer", False),
        ("LinkedIn", "Software Engineering Volunteer", False),
        ("LinkedIn", "Senior Software Engineer", False),
    ],
)
def test_is_internship(source, title, keep):
    assert is_internship(job(source, title)) is keep


def test_intern_in_title_beats_newgrad_signal():
    # A title containing "Intern" is kept even if it also has a droppable word.
    assert is_internship(job("x", "Senior-team Software Engineer Intern")) is True


def test_filter_counts():
    jobs = [
        job("Simplify", "SWE Intern"),
        job("LinkedIn", "Software Developer"),
        job("speedyapply-newgrad-usa", "New Grad Engineer"),
    ]
    kept, dropped = filter_internships(jobs)
    assert len(kept) == 1 and dropped == 2


class TestWordBoundaries:
    """The intern regex had no word boundaries, so it matched "Internal",
    "International", "Cooper" and "Cooperative" -- classifying them as
    internships and short-circuiting every other check."""

    @pytest.mark.parametrize("title", [
        "Internal Audit Analyst",
        "Internally Facing Tools Engineer",
        "Cooper Standard Manufacturing Engineer",
        "Cooperative Education Coordinator",
        "Chief Cooperation Officer",
    ])
    def test_substring_lookalikes_are_not_internships(self, title):
        # A repo source falls through to the new-grad check; LinkedIn requires
        # the word explicitly. Neither may be fooled by a substring.
        assert is_internship(job("LinkedIn", title)) is False, title

    @pytest.mark.parametrize("title", [
        "Software Engineer Intern",
        "Software Engineering Interns",
        "Software Engineering Internship",
        "2027 Software Internships",
        "Software Engineer Co-op",
        "Software Engineer Co-Op",
        "Software Engineer Co op",
        "Software Engineer Coop",
        "Software Engineering Co-ops",
    ])
    def test_real_spellings_all_match(self, title):
        assert is_internship(job("LinkedIn", title)) is True, title

    def test_international_still_matches_via_intern(self):
        """"International" contains "intern" but is not one; with boundaries it
        no longer short-circuits, so LinkedIn correctly drops it."""
        assert is_internship(job("LinkedIn", "International Sales Analyst")) is False
