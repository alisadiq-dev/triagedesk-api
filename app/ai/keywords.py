"""Simple keyword rules for the priority when the AI is unavailable (fallback only)."""

import re

from app.models.enums import Priority

# Checked from most to least severe; the first matching group wins. Whole words only.
_RULES: list[tuple[Priority, tuple[str, ...]]] = [
    (
        Priority.URGENT,
        ("outage", "down for all", "security breach", "breach", "data loss", "data lost", "hacked"),
    ),
    (
        Priority.HIGH,
        ("cannot", "can't", "unable", "error", "failed", "failure", "charged twice", "locked out"),
    ),
    (
        Priority.LOW,
        ("question", "how do i", "how to", "feature request", "suggestion", "would be nice"),
    ),
]


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def keyword_priority(title: str, description: str) -> Priority:
    text = f"{title}\n{description}".lower()
    for priority, phrases in _RULES:
        if any(_contains(text, phrase) for phrase in phrases):
            return priority
    return Priority.MEDIUM
