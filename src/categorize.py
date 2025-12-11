from __future__ import annotations

import re
from typing import Dict, Tuple

CATEGORIES: Dict[str, Dict[str, int]] = {
    "Fuel": {
        "exxon": 3, "shell": 3, "chevron": 3, "bp": 3, "sunoco": 3,
        "gas": 2, "diesel": 2, "fuel": 2, "station": 1
    },
    "Tools & Equipment": {
        "harbor freight": 4, "tool": 2, "drill": 2, "saw": 2, "dewalt": 2, "milwaukee": 2,
        "equipment": 2, "hardware": 1
    },
    "Materials / Supplies": {
        "home depot": 4, "lowe": 4, "lumber": 2, "mulch": 2, "concrete": 2, "pipe": 2,
        "paint": 2, "supply": 1, "materials": 2
    },
    "Vehicle Maintenance": {
        "autozone": 4, "advance auto": 4, "jiffy lube": 4, "oil": 2, "tire": 2,
        "brake": 2, "battery": 2, "maintenance": 1, "alignment": 2
    },
    "Meals": {
        "restaurant": 2, "grill": 1, "cafe": 1, "mcdonald": 2, "chipotle": 2, "starbucks": 2,
        "meal": 2, "dinner": 1, "lunch": 1
    },
    "Office / Admin": {
        "staples": 4, "office": 2, "paper": 1, "printer": 2, "ink": 2,
        "subscription": 2, "software": 2, "zoom": 2
    },
    "Subcontractors": {
        "subcontract": 4, "1099": 3, "labor": 2, "contractor": 2
    },
    "Permits / Fees": {
        "permit": 4, "license": 2, "fee": 2, "inspection": 2
    },
}

DEFAULT_CATEGORY = "Other"

def categorize(raw_text: str, vendor: str = "") -> Tuple[str, float]:
    """
    Returns (category, confidence 0..1)
    Confidence is a rough heuristic: winning_score / (sum_scores + epsilon)
    """
    t = (vendor + "\n" + raw_text).lower()
    t = re.sub(r"[^a-z0-9\s&']", " ", t)
    t = re.sub(r"\s+", " ", t)

    scores = {cat: 0 for cat in CATEGORIES.keys()}
    for cat, rules in CATEGORIES.items():
        for kw, w in rules.items():
            if kw in t:
                scores[cat] += w

    best_cat = DEFAULT_CATEGORY
    best_score = 0
    total = 0
    for cat, sc in scores.items():
        total += sc
        if sc > best_score:
            best_score = sc
            best_cat = cat

    if best_score == 0:
        return DEFAULT_CATEGORY, 0.25

    confidence = best_score / (total + 1e-9)
    # clamp into a nicer range
    confidence = max(0.40, min(0.95, confidence))
    return best_cat, float(confidence)

def all_categories():
    return ["All"] + sorted(list(CATEGORIES.keys()) + [DEFAULT_CATEGORY])

