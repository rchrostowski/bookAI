def categorize(raw_text: str, vendor: str = "", memory: dict | None = None):
    """
    Returns:
      {
        category: str,
        confidence: float,
        reasons: list[str],
        learned_from: int
      }
    """

    memory = memory or {}
    vendor_key = (vendor or "").strip().lower()

    # Known vendor rule
    if vendor_key and vendor_key in memory.get("vendors", {}):
        v = memory["vendors"][vendor_key]
        return {
            "category": v.get("category", "Other"),
            "confidence": 0.95,
            "reasons": [
                "Auto-approved based on past receipts",
                f"Seen {v.get('count', 1)} similar receipts"
            ],
            "learned_from": v.get("count", 1)
        }

    text = (raw_text or "").lower()

    rules = [
        ("fuel", "Fuel"),
        ("gas", "Fuel"),
        ("coffee", "Meals"),
        ("restaurant", "Meals"),
        ("bar", "Meals"),
        ("grill", "Meals"),
        ("home depot", "Materials / Supplies"),
        ("lowes", "Materials / Supplies"),
        ("supply", "Materials / Supplies"),
        ("tools", "Tools & Equipment"),
        ("repair", "Vehicle Maintenance"),
        ("office", "Office / Admin"),
    ]

    for key, cat in rules:
        if key in text:
            return {
                "category": cat,
                "confidence": 0.82,
                "reasons": [
                    "Matched common receipt pattern",
                    "Will auto-approve next time"
                ],
                "learned_from": 0
            }

    return {
        "category": "Other",
        "confidence": 0.35,
        "reasons": [
            "New vendor",
            "Needs one review to learn"
        ],
        "learned_from": 0
    }



