from __future__ import annotations

import re
from typing import Dict, Any, Optional, List, Tuple


# -----------------------------------------------------------------------------
# Vendor / text rules (fast + deterministic)
# -----------------------------------------------------------------------------
# These rules run BEFORE any memory / heuristic logic.
# Put your "never get cooked" merchants here.
VENDOR_RULES: List[Tuple[re.Pattern, str, str]] = [
    # Books / retail
    (re.compile(r"\b(barnes\s*&\s*noble|barnes\b|noble\b|bn\.com|bookseller|bookstore)\b", re.I),
     "Office / Admin", "Books / retail purchase"),

    # Gas / fuel stations
    (re.compile(r"\b(shell|exxon|mobil|sunoco|wawa|sheetz|bp|citgo|chevron|valero|texaco|speedway|circle\s*k)\b", re.I),
     "Fuel", "Fuel station vendor"),

    # Meals / restaurants / coffee
    (re.compile(r"\b(starbucks|dunkin|mcdonald'?s|burger\s*king|wendy'?s|chipotle|panera|subway|taco\s*bell|kfc|pizza)\b", re.I),
     "Meals", "Restaurant / food vendor"),

    # Home improvement / materials
    (re.compile(r"\b(home\s*depot|lowe'?s|ace\s*hardware|tractor\s*supply|menards)\b", re.I),
     "Materials / Supplies", "Home improvement / supplies vendor"),

    # Tools / equipment
    (re.compile(r"\b(harbor\s*freight|grainger|fastenal)\b", re.I),
     "Tools & Equipment", "Tool / equipment vendor"),
]

# Keyword rules (raw text based)
TEXT_RULES: List[Tuple[re.Pattern, str, str]] = [
    # Fuel patterns (gallons / price per gallon)
    (re.compile(r"\b(gal(lon)?s?|price\s*/\s*gal|/gal|ppg|unleaded|diesel)\b", re.I),
     "Fuel", "Fuel terms in receipt"),

    # Meals patterns
    (re.compile(r"\b(gratuity|tip|server|table|dine|eatery)\b", re.I),
     "Meals", "Dining terms in receipt"),

    # Subcontractors patterns
    (re.compile(r"\b(labor|subcontract(or)?|sub\s*contract|1099|installer)\b", re.I),
     "Subcontractors", "Labor/subcontractor terms"),

    # Permits/fees patterns
    (re.compile(r"\b(permit|license|licen[cs]e|fee|inspection|registration|dmv|clerk)\b", re.I),
     "Permits / Fees", "Permit/fee terms in receipt"),

    # Vehicle maintenance patterns
    (re.compile(r"\b(oil\s*change|tire|tires|alignment|brake|brakes|mechanic|service\s*center)\b", re.I),
     "Vehicle Maintenance", "Vehicle service terms"),

    # Office/admin patterns
    (re.compile(r"\b(office|paper|printer|ink|staples|shipping|postage|ups|fedex|usps)\b", re.I),
     "Office / Admin", "Office/admin terms"),
]

# Negative rules: if these appear, DO NOT choose certain categories.
# Example: avoid "Permits / Fees" when it’s clearly a retail receipt.
ANTI_PERMITS_HINTS = re.compile(r"\b(visa|discover|mastercard|amex|subtotal|sales\s*tax|total)\b", re.I)


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s&./-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _memory_suggest(vendor: str, memory: Optional[dict]) -> Optional[Dict[str, Any]]:
    """
    Uses your workspace memory if available.
    This expects memory to be a dict, but is defensive to avoid crashes.
    We try common shapes:
      memory["vendor_map"][<normalized_vendor>] -> {category, account_code, ...}
      memory["vendor_mappings"] -> list/dict
    """
    if not memory:
        return None

    vkey = _norm(vendor)
    if not vkey:
        return None

    # Shape 1: vendor_map dict
    vm = memory.get("vendor_map")
    if isinstance(vm, dict):
        hit = vm.get(vkey)
        if isinstance(hit, dict) and hit.get("category"):
            return {
                "category": hit["category"],
                "confidence": 0.92,
                "reasons": ["Learned from your workspace (vendor mapping)"],
            }

    # Shape 2: vendor_mappings list of dicts
    vml = memory.get("vendor_mappings")
    if isinstance(vml, list):
        for row in vml:
            if not isinstance(row, dict):
                continue
            if _norm(row.get("vendor", "")) == vkey and row.get("category"):
                return {
                    "category": row["category"],
                    "confidence": 0.90,
                    "reasons": ["Learned from your workspace (vendor mappings)"],
                }

    return None


def _rule_suggest(raw_text: str, vendor: str) -> Optional[Dict[str, Any]]:
    """
    Hard rules first: vendor rules, then text rules.
    """
    raw = raw_text or ""
    v = vendor or ""

    # Vendor rules (best)
    for rx, cat, why in VENDOR_RULES:
        if rx.search(v) or rx.search(raw):
            return {"category": cat, "confidence": 0.92, "reasons": [f"Vendor rule: {why}"]}

    # Text rules (good fallback)
    for rx, cat, why in TEXT_RULES:
        if rx.search(raw):
            # guard: don’t call it permits/fees just because "fee" appears in payment garbage
            if cat == "Permits / Fees" and ANTI_PERMITS_HINTS.search(raw):
                continue
            return {"category": cat, "confidence": 0.78, "reasons": [f"Receipt text rule: {why}"]}

    return None


def categorize(raw_text: str, vendor: str = "", memory: Optional[dict] = None) -> Dict[str, Any]:
    """
    Returns:
      {"category": str, "confidence": float, "reasons": [str, ...]}

    Categories expected by your app (COA keys):
      Fuel, Meals, Materials / Supplies, Tools & Equipment, Vehicle Maintenance,
      Office / Admin, Subcontractors, Permits / Fees, Other
    """

    # 1) Hard deterministic rules
    rule_hit = _rule_suggest(raw_text, vendor)
    if rule_hit:
        return rule_hit

    # 2) Memory-based mapping (what you approved before)
    mem_hit = _memory_suggest(vendor, memory)
    if mem_hit:
        return mem_hit

    # 3) Heuristic fallback
    text = (raw_text or "").lower()
    reasons: List[str] = []

    # Simple scoring
    scores = {
        "Fuel": 0,
        "Meals": 0,
        "Materials / Supplies": 0,
        "Tools & Equipment": 0,
        "Vehicle Maintenance": 0,
        "Office / Admin": 0,
        "Subcontractors": 0,
        "Permits / Fees": 0,
        "Other": 0,
    }

    def bump(cat: str, n: int, why: str):
        scores[cat] += n
        reasons.append(why)

    # Fuel
    if any(k in text for k in ["gallon", "gal", "/gal", "unleaded", "diesel", "pump", "ppg"]):
        bump("Fuel", 3, "Detected fuel terms (gal/pump/diesel/etc.)")

    # Meals
    if any(k in text for k in ["restaurant", "grill", "cafe", "coffee", "gratuity", "tip"]):
        bump("Meals", 2, "Detected dining terms (restaurant/cafe/tip/etc.)")

    # Materials/Supplies
    if any(k in text for k in ["lumber", "plywood", "concrete", "drywall", "paint", "screws", "nails"]):
        bump("Materials / Supplies", 2, "Detected materials terms (lumber/paint/hardware items)")

    # Tools/Equipment
    if any(k in text for k in ["drill", "saw", "tool", "battery", "charger", "ladder", "compressor"]):
        bump("Tools & Equipment", 2, "Detected tool/equipment terms")

    # Vehicle maintenance
    if any(k in text for k in ["oil change", "tire", "tires", "brake", "alignment", "service center"]):
        bump("Vehicle Maintenance", 2, "Detected vehicle service terms")

    # Office/Admin
    if any(k in text for k in ["office", "paper", "printer", "ink", "shipping", "postage", "staples"]):
        bump("Office / Admin", 2, "Detected office/admin terms")

    # Subcontractors
    if any(k in text for k in ["labor", "subcontract", "installer", "1099"]):
        bump("Subcontractors", 2, "Detected labor/subcontractor terms")

    # Permits/Fees
    if any(k in text for k in ["permit", "license", "inspection", "registration", "dmv", "clerk", "fee"]):
        # guard: avoid payment “fee” noise on retail receipts
        if not ANTI_PERMITS_HINTS.search(text):
            bump("Permits / Fees", 2, "Detected permits/fees terms")
        else:
            # don’t add reason; too noisy
            pass

    # Pick best
    best_cat = max(scores.items(), key=lambda kv: kv[1])[0]
    best_score = scores[best_cat]

    # Confidence mapping
    if best_score >= 3:
        conf = 0.70
    elif best_score == 2:
        conf = 0.60
    elif best_score == 1:
        conf = 0.45
    else:
        best_cat = "Other"
        conf = 0.35
        reasons = ["No strong category signals found"]

    # Keep reasons short and unique
    uniq: List[str] = []
    seen = set()
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
        if len(uniq) >= 4:
            break

    return {"category": best_cat, "confidence": float(conf), "reasons": uniq}



