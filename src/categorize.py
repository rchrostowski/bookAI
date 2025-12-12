from __future__ import annotations

from typing import Dict, List, Optional


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _memory_vendor_hit(memory: dict, vendor: str) -> Optional[Dict]:
    """
    Expected memory structure (based on remember_vendor_mapping usage):
      memory["vendors"][vendor_lower] = {"category": "...", "account_code": "...", "count": int}
    """
    try:
        vendors = memory.get("vendors", {})
        return vendors.get(_norm(vendor))
    except Exception:
        return None


def categorize(raw_text: str, vendor: str = "", memory: dict | None = None) -> Dict:
    """
    Backward compatible return:
      {
        "category": str,
        "confidence": float,
        "reasons": list[str],
        # extras (optional for UI)
        "learned_from": int,
        "auto_approved": bool,
      }
    """
    memory = memory or {}
    text = _norm(raw_text)

    # -------------------------
    # 1) Strongest signal: learned vendor mapping
    # -------------------------
    vhit = _memory_vendor_hit(memory, vendor)
    if vhit:
        cat = vhit.get("category") or "Other"
        cnt = int(vhit.get("count") or 1)
        return {
            "category": cat,
            "confidence": 0.95,
            "reasons": [
                "Auto-approved from your history",
                f"Learned from {cnt} prior receipt(s) for this vendor",
            ],
            "learned_from": cnt,
            "auto_approved": True,
        }

    # -------------------------
    # 2) Keyword heuristics (fast + surprisingly good)
    # -------------------------
    rules = [
        (["shell", "exxon", "chevron", "bp", "sunoco", "wawa", "gas", "fuel"], "Fuel", 0.84),
        (["coffee", "cafe", "dunkin", "starbucks", "restaurant", "grill", "pizza", "bar "], "Meals", 0.82),
        (["home depot", "homedepot", "lowe", "ace hardware", "lumber", "supply", "materials"], "Materials / Supplies", 0.82),
        (["tool", "tools", "equipment", "drill", "saw", "dewalt", "milwaukee"], "Tools & Equipment", 0.80),
        (["oil change", "tire", "tires", "auto", "repair", "service center", "mechanic"], "Vehicle Maintenance", 0.80),
        (["office", "staples", "printer", "paper", "shipping", "usps", "ups", "fedex"], "Office / Admin", 0.78),
        (["subcontract", "subcontractor", "labor", "contractor"], "Subcontractors", 0.78),
        (["permit", "license", "fee", "fees"], "Permits / Fees", 0.76),
    ]

    for keys, cat, conf in rules:
        if any(k in text for k in keys) or any(k in _norm(vendor) for k in keys):
            auto = conf >= 0.80
            reasons = ["Matched common receipt pattern"]
            if auto:
                reasons.append("Will auto-approve next time after one clean save")
            else:
                reasons.append("One quick review will teach BookIQ this vendor")
            return {
                "category": cat,
                "confidence": float(conf),
                "reasons": reasons,
                "learned_from": 0,
                "auto_approved": auto,
            }

    # -------------------------
    # 3) Default fallback
    # -------------------------
    return {
        "category": "Other",
        "confidence": 0.35,
        "reasons": [
            "New vendor / unclear receipt",
            "Approve once and BookIQ will remember it",
        ],
        "learned_from": 0,
        "auto_approved": False,
    }



