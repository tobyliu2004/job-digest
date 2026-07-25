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
