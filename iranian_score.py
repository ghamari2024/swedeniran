"""Offline heuristic scoring of how likely a full name is Iranian.

The decision is driven mainly by the *surname*, because the first name is
always an Iranian match (it is the search source) and therefore not a useful
discriminator. Score is 0..100; higher means more likely Iranian.
"""

from __future__ import annotations

import re

# Strong Iranian surname endings.
IRANIAN_SUFFIXES = (
    "zadeh", "zade", "nezhad", "nejad", "pour", "poor", "pur",
    "ian", "yan", "abadi", "khani", "vand", "lou", "loo",
)

# Common Iranian surname stems / whole surnames.
IRANIAN_SURNAME_TOKENS = {
    "hosseini", "hoseini", "mohammadi", "mohamadi", "rezaei", "rezai",
    "ahmadi", "karimi", "moradi", "tehrani", "jafari", "jaafari", "sadeghi",
    "ghasemi", "kazemi", "alavi", "amini", "akbari", "asadi", "bagheri",
    "ebrahimi", "esmaili", "fazeli", "ghorbani", "hashemi", "heydari",
    "jalali", "kamali", "mahmoudi", "mansouri", "mirzaei", "mostafavi",
    "naderi", "najafi", "nikkhah", "rahimi", "rashidi", "sadr", "salehi",
    "shahbazi", "soltani", "tabatabai", "yazdani", "zarei", "zandi",
    "farahani", "golshani", "kiani", "nouri", "sharifi", "shirazi",
}

# Common Scandinavian / Swedish surname endings (strong negative).
SWEDISH_SUFFIXES = (
    "sson", "berg", "strom", "ström", "qvist", "kvist", "lund", "gren",
    "dahl", "blad", "holm", "stedt", "stad", "mark", "borg", "fors",
    "vall", "sten", "lind", "hammar", "sjo", "sjö", "rud", "berga",
    "lof", "löf", "wall", "ström", "kvist", "dotter",
)

# Common Swedish / Western given names (negative when they appear as a
# middle or surname-position token).
SWEDISH_GIVEN_NAMES = {
    "erik", "lars", "karl", "carl", "anders", "per", "johan", "sven",
    "nils", "gunnar", "bengt", "bo", "lennart", "olof", "olov", "gustav",
    "magnus", "fredrik", "henrik", "jan", "mats", "stefan", "thomas",
    "peter", "mikael", "michael", "daniel", "andreas", "anna", "maria",
    "eva", "karin", "lena", "birgitta", "kristina", "christina", "ingrid",
    "margareta", "elisabeth", "kerstin", "marie", "ulla", "inger",
    "gunilla", "sara", "emma", "johanna", "linnea", "sofia", "helena",
    "william", "lucas", "oscar", "axel", "gustaf",
}


def _tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ\-' ]", " ", name or "")
    return [t for t in re.split(r"[\s\-']+", cleaned.lower()) if t]


def _looks_iranian_surname(token: str) -> bool:
    if token in IRANIAN_SURNAME_TOKENS:
        return True
    return any(token.endswith(suf) for suf in IRANIAN_SUFFIXES)


def _looks_swedish_surname(token: str) -> bool:
    return any(token.endswith(suf) for suf in SWEDISH_SUFFIXES)


def iranian_score(name: str) -> int:
    """Return 0..100 likelihood the full name belongs to an Iranian person."""
    tokens = _tokens(name)
    if not tokens:
        return 50

    surname = tokens[-1]
    middles = tokens[1:-1] if len(tokens) > 2 else []

    score = 50

    if _looks_iranian_surname(surname):
        score += 42
    elif _looks_swedish_surname(surname):
        score -= 45

    # Swedish given name sitting in middle/last position is a strong signal
    # the person is Swedish with only an Iranian-looking first name.
    swedish_middles = sum(1 for t in middles if t in SWEDISH_GIVEN_NAMES)
    if surname in SWEDISH_GIVEN_NAMES:
        swedish_middles += 1
    score -= min(swedish_middles, 2) * 18

    # An Iranian token anywhere (besides the matched first name) nudges up.
    if len(tokens) > 1 and any(_looks_iranian_surname(t) for t in tokens[1:]):
        score += 6

    return max(0, min(100, score))
