from __future__ import annotations

import re
from typing import Dict, List, Tuple


# -------------------------
# Category keyword rules
# -------------------------

CATEGORY_RULES: Dict[str, Dict[str, List[str]]] = {
    "Fuel": {
        "vendor": [
            "shell", "exxon", "bp", "chevron", "sunoco", "mobil",
            "citgo", "wawa", "speedway", "marathon"
        ],
        "text": [
            "fuel", "gas", "unleaded", "diesel", "pump", "gallon"
        ],
    },
    "Meals": {
        "vendor": [
            "starbucks", "dunkin", "coffee", "cafe", "grill",
            "restaurant", "bistro", "kitchen", "bar", "pizza"
        ],
        "text": [
            "coffee", "latte", "espresso", "bagel", "sandwich",
            "burger", "fries", "meal", "food", "drink", "soda"
        ],
    },
    "Materials / Supplies": {
        "vendor": [
            "home depot", "lowe", "ace hardware", "menards"
        ],
        "text": [
            "lumber", "mulch", "concrete", "pipe", "paint",
            "supply", "supplies", "hardware", "materials"
        ],
    },
    "Tools & Equipment": {
        "vendor": [
            "harbor freight", "grainger", "fastenal"
        ],
        "text": [
            "tool", "drill", "saw", "equipment", "ladder",
            "compressor", "generator"
        ],
    },
    "Vehicle Maintenance": {
        "vendor": [
            "autozone", "advanced auto", "jiffy lube", "pep boys"
        ],
        "text": [
            "oil change", "tire", "brake", "alignment",
            "maintenance", "service"
        ],
    },
    "Office / Admin": {
        "vendor": [
            "staples", "office depot", "ups", "fedex"
        ],
        "text": [
            "paper", "printer", "ink", "shipping", "postage",
            "admin", "office"
        ],
    },
    "Subcontractors": {
        "vendor": [],
        "text": [
            "labor", "contractor", "subcontractor", "install",
            "installation", "service fee"
        ],
    },
    "Permits / Fees": {
        "vendor": [],
        "text": [
            "permit", "license", "inspection", "city fee", "county"
        ],
    },
}


ALL_CATEGORIES = ["All"] + list(CATEGORY_RULES.keys()) + ["Other"]


# -------------------------
# Helpers
# -------------------------

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def _count_hits(haystack: str, needles: List[str]) -> int:
    return sum(1 for n in needles if n in haystack)


# -------------------------
# Public API
# -------------------------

def all_categories() -> List[str]:
    return ALL_CATEGORIES


def categorize(raw_text: str, vendor: str = "") -> Tuple[str, float]:
    """
    Returns (category, confidence)

    Confidence logic (simple & explainable):
    - Vendor hit = strong signal
    - Text keyword hits = medium signal
    - Confidence scaled to max ~0.95
    """

    text = _normalize(raw_text)
    vendor_norm = _normalize(vendor)

    best_category = "Other"
    best_score = 0

    for category, rules in CATEGORY_RULES.items():
        score = 0

        # Vendor match (very strong)
        if vendor_norm:
            score += 3 * _count_hits(vendor_norm, rules["vendor"])

        # OCR text keyword matches
        score += _count_hits(text, rules["text"])

        if score > best_score:
            best_score = score
            best_category = category

    # -------------------------
    # Confidence scaling
    # -------------------------

    if best_score == 0:
        # No useful signal
        return "Other", 0.25

    # Score → confidence mapping
    # 1 hit  → ~0.55
    # 2 hits → ~0.70
    # 3 hits → ~0.85
    # 4+     → ~0.95
    confidence = min(0.95, 0.40 + 0.15 * best_score)

    return best_category, round(confidence, 2)

