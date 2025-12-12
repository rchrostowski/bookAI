from __future__ import annotations

import re
from typing import Dict

from src.memory import get_vendor_mapping

KEYWORDS = {
    "Fuel": ["gas", "fuel", "shell", "exxon", "chevron", "bp", "sunoco", "wawa"],
    "Meals": ["restaurant", "cafe", "coffee", "diner", "pizza", "grill", "bar", "taco"],
    "Materials / Supplies": ["supply", "supplies", "hardware", "lumber", "home depot", "lowe", "ace", "tools"],
    "Tools & Equipment": ["drill", "saw", "tool", "equipment", "battery", "charger"],
    "Vehicle Maintenance": ["oil", "tire", "auto", "repair", "mechanic", "service", "inspection"],
    "Office / Admin": ["software", "subscription", "office", "paper", "printer", "internet", "phone"],
    "Subcontractors": ["subcontract", "1099", "labor", "installer"],
    "Permits / Fees": ["permit", "license", "fee", "registration"],
}

TOTAL_WORDS = ["total", "amount due", "balance due", "grand total", "total due"]

def categorize(raw_text: str, vendor: str = "", memory: Dict | None = None, coa: Dict | None = None) -> Dict:
    """
    Returns:
      { "category": str, "confidence": float, "reasons": [str] }
    """
    text = (raw_text or "").lower()
    v = (vendor or "").lower()

    reasons = []
    score = 0.0

    # 1) Vendor memory (strongest signal)
    if memory is not None and vendor:
        vm = get_vendor_mapping(memory, vendor)
        if vm and vm.get("category"):
            reasons.append("Matched saved vendor rule")
            return {"category": vm["category"], "confidence": 0.95, "reasons": reasons}

    # 2) Presence of TOTAL label
    if any(t in text for t in TOTAL_WORDS):
        score += 0.25
        reasons.append("Found TOTAL label")
    else:
        reasons.append("No clear TOTAL label")

    # 3) Date-like pattern
    if re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text) or re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})\b", text):
        score += 0.20
        reasons.append("Found date pattern")
    else:
        reasons.append("No clear date pattern")

    # 4) Keyword scoring
    best_cat = "Other"
    best_hits = 0

    blob = (v + "\n" + text).lower()

    for cat, kws in KEYWORDS.items():
        hits = 0
        for kw in kws:
            if kw in blob:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_cat = cat

    if best_hits > 0:
        score += min(0.45, 0.15 * best_hits)
        reasons.append(f"Keyword match for {best_cat} ({best_hits} hit(s))")
    else:
        reasons.append("No strong keyword match → Other")

    # 5) Final confidence shaping
    # baseline
    conf = max(0.35, min(0.90, score + 0.20))

    return {"category": best_cat, "confidence": float(conf), "reasons": reasons}


