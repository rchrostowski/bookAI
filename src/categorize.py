from __future__ import annotations

import re
from typing import Dict, Tuple

from src.memory import get_vendor_mapping

KEYWORDS = {
    "Meals": ["coffee", "cafe", "espresso", "latte", "cappuccino", "bakery", "restaurant", "diner", "pizza", "grill", "bar"],
    "Fuel": ["gas", "fuel", "shell", "exxon", "chevron", "bp", "sunoco", "wawa"],
    "Materials / Supplies": ["supply", "supplies", "hardware", "lumber", "home depot", "lowe", "lowes", "ace"],
    "Tools & Equipment": ["tool", "tools", "drill", "saw", "equipment", "battery", "charger"],
    "Vehicle Maintenance": ["oil", "tire", "auto", "repair", "mechanic", "service", "inspection"],
    "Office / Admin": ["software", "subscription", "office", "paper", "printer", "internet", "phone"],
    "Subcontractors": ["subcontract", "1099", "labor", "installer"],
    "Permits / Fees": ["permit", "license", "fee", "registration"],
}

TOTAL_PATTERNS = [
    r"\btotal\b", r"\bgrand\s*total\b", r"\bamount\s*due\b", r"\bbalance\s*due\b", r"\btotal\s*due\b",
    r"\bamt\s*due\b", r"\btotai\b", r"\bto\s*tal\b"
]
DATE_PATTERNS = [
    r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b\d{1,2}[/-]\d{1,2}[/-](\d{2}|\d{4})\b",
]
MONEY_PATTERN = r"(?<!\d)(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2}))"


def categorize(raw_text: str, vendor: str = "", memory: Dict | None = None) -> Dict:
    text = (raw_text or "").lower()
    v = (vendor or "").strip()
    vlow = v.lower()

    reasons = []

    # 1) Vendor memory = instant high confidence
    if memory is not None and v:
        vm = get_vendor_mapping(memory, v)
        if vm and vm.get("category"):
            reasons.append("Matched saved vendor rule")
            return {"category": vm["category"], "confidence": 0.95, "reasons": reasons}

    # 2) Vendor-only classification (fixes your Joe’s Coffee case)
    v_cat, v_hits = _best_category_from_blob(vlow)
    if v_hits >= 1:
        reasons.append(f"Vendor keyword match for {v_cat} ({v_hits} hit(s))")
        return {"category": v_cat, "confidence": 0.85, "reasons": reasons}

    # 3) Text signals
    has_total = any(re.search(p, text) for p in TOTAL_PATTERNS)
    has_date = any(re.search(p, text) for p in DATE_PATTERNS)
    has_money = bool(re.search(MONEY_PATTERN, text))

    if has_total: reasons.append("Found TOTAL-like label")
    else: reasons.append("No clear TOTAL label")
    if has_date: reasons.append("Found date pattern")
    else: reasons.append("No clear date pattern")
    if has_money: reasons.append("Found money amounts")
    else: reasons.append("No money amounts found")

    # 4) Text keyword classification
    t_cat, t_hits = _best_category_from_blob(text)
    if t_hits:
        reasons.append(f"Text keyword match for {t_cat} ({t_hits} hit(s))")
    else:
        reasons.append("No strong keyword match → Other")

    # 5) Confidence scoring (more generous than before)
    conf = 0.35
    if has_money: conf += 0.18
    if has_date: conf += 0.20
    if has_total: conf += 0.25
    conf += min(0.20, 0.07 * t_hits)

    conf = max(0.35, min(0.92, conf))

    return {"category": t_cat, "confidence": float(conf), "reasons": reasons}


def _best_category_from_blob(blob: str) -> Tuple[str, int]:
    blob = (blob or "").lower()
    best_cat = "Other"
    best_hits = 0
    for cat, kws in KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in blob)
        if hits > best_hits:
            best_hits = hits
            best_cat = cat
    return best_cat, best_hits



