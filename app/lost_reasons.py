"""Sort Pipedrive lost reasons into the team's fixed categories.

The categories are the options in Pipedrive's lost-reason list. Deals lost
before that list existed carry free text ("ghosted", "no budget", "went with
a Korean agency"), so those are matched by keyword. The rules run in order
and the first match wins: "chose another agency, they were cheaper" is a
competitor loss before it is a price one. Text that fits nothing is left
out rather than forced into a category.
"""
from __future__ import annotations

CATEGORIES = (
    "No response / went cold",
    "Procurement / Legal issue",
    "Client went in-house",
    "Scope not a fit",
    "Campaign cancelled",
    "No internal approval",
    "Lost to competitor",
    "Timing / Not ready yet",
    "Price too high",
)

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Lost to competitor", ("compet", "another agency", "different agency", "agency instead",
                            "agencies", "went with", "other options", "other channels")),
    ("Client went in-house", ("in-house", "inhouse", "in house")),
    ("Procurement / Legal issue", ("procure", "legal", "contract", "rfp", "rfi", "compliance")),
    ("No response / went cold", ("ghost", "blank", "respon", "repson", "silent", "quiet",
                                 "stall", "no repl", "cold", "trail")),
    ("Price too high", ("budget", "price", "money", "cheaper", "expensive", "cost", "not worth")),
    ("Timing / Not ready yet", ("timing", "timeline", "not ready", "on hold", "right now",
                                "next year", "later")),
    ("Campaign cancelled", ("cancel", "stopped", "project change", "brief")),
    ("No internal approval", ("approval", "declined", "decided", "mind", "strateg", "stragetic",
                              "re-shuffle", "reshuffle", "poc", "point of contact",
                              "person change", "exec")),
    ("Scope not a fit", ("scope", "fit", "influencer selection", "performance", "case stud",
                         "proposal", "delivery", "direction", "interest", "long shot")),
)


def categorise(reason: str | None) -> str | None:
    """The category for a lost reason, or None when there is none to give."""
    text = (reason or "").strip().lower()
    if not text:
        return None
    for category in CATEGORIES:
        if text == category.lower():
            return category
    for category, words in RULES:
        if any(word in text for word in words):
            return category
    return None
