import pytest

from app.ai.keywords import keyword_priority
from app.models.enums import Priority


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        ("Production outage", "Everything is down for all users", Priority.URGENT),
        ("Possible security breach", "We saw strange logins", Priority.URGENT),
        ("Data loss", "Our records disappeared", Priority.URGENT),
        ("Cannot log in", "I get an error every time", Priority.HIGH),
        ("Charged twice", "Please fix the billing error", Priority.HIGH),
        ("Question about invoices", "How do I download one?", Priority.LOW),
        ("Feature request", "It would be nice to have dark mode", Priority.LOW),
        ("Hello", "Just checking in", Priority.MEDIUM),
    ],
)
def test_keyword_rules_pick_a_priority(title: str, description: str, expected: Priority) -> None:
    assert keyword_priority(title, description) == expected


def test_the_most_severe_matching_rule_wins() -> None:
    assert (
        keyword_priority("Question: is our data lost?", "data loss after the outage")
        == Priority.URGENT
    )


def test_matching_ignores_case_and_whole_word_boundaries() -> None:
    assert keyword_priority("OUTAGE", "x") == Priority.URGENT
    assert keyword_priority("Our shoutage plan", "nothing") == Priority.MEDIUM
