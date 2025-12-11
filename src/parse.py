from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple

from dateutil import parser as dtparser

TOTAL_HINTS = [
    "total", "amount due", "balance due", "grand total", "total due", "amt due"
]

def clean_text(t: str) -> str:
    return re.sub(r"[ \t]+", " ", t.replace("\r", "\n")).strip()

def guess_vendor(raw_text: str) -> str:
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
    if not lines:
        return ""
    # often the first non-empty line is vendor
    vendor = lines[0]
    # trim weird OCR artifacts
    vendor = re.sub(r"[^A-Za-z0-9&\-\.\' ]", "", vendor).strip()
    return vendor[:60]

def parse_amount(raw_text: str) -> Optional[float]:
    t = raw_text.lower()

    # 1) Try to find an amount near "total" style keywords
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    money_re = re.compile(r"(\$?\s*\d{1,6}(?:[,\s]\d{3})*(?:\.\d{2})?)")
    best: Optional[float] = None

    for ln in lines:
        if any(h in ln for h in TOTAL_HINTS):
            candidates = money_re.findall(ln)
            for c in candidates[::-1]:
                amt = _to_float(c)
                if amt is not None:
                    best = amt
                    break
        if best is not None:
            break

    if best is not None:
        return best

    # 2) Fallback: pick the largest plausible money amount in the receipt
    candidates = money_re.findall(t)
    amounts = []
    for c in candidates:
        amt = _to_float(c)
        if amt is not None and 0.01 <= amt <= 50000:
            amounts.append(amt)

    if not amounts:
        return None
    return max(amounts)

def _to_float(s: str) -> Optional[float]:
    s = s.replace("$", "").replace(" ", "").replace(",", "")
    try:
        return float(s)
    except Exception:
        return None

def parse_date(raw_text: str) -> Optional[str]:
    """
    Returns ISO date string YYYY-MM-DD if found.
    """
    t = raw_text

    # common explicit date formats
    patterns = [
        r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",     # 12/31/2025
        r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b",       # 2025-12-31
        r"\b([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})\b",  # Dec 31, 2025
    ]

    for p in patterns:
        m = re.search(p, t)
        if m:
            cand = m.group(1)
            dt = _safe_parse_date(cand)
            if dt:
                return dt.strftime("%Y-%m-%d")

    # fallback: dateutil fuzzy parse from whole text (can be noisy)
    dt = _safe_parse_date(t, fuzzy=True)
    if dt:
        # protect against absurd dates
        if 1990 <= dt.year <= datetime.now().year + 1:
            return dt.strftime("%Y-%m-%d")

    return None

def _safe_parse_date(s: str, fuzzy: bool = False) -> Optional[datetime]:
    try:
        return dtparser.parse(s, fuzzy=fuzzy, dayfirst=False)
    except Exception:
        return None

def extract_fields(raw_text: str) -> Tuple[str, Optional[str], Optional[float]]:
    raw_text = clean_text(raw_text)
    vendor = guess_vendor(raw_text)
    date = parse_date(raw_text)
    amount = parse_amount(raw_text)
    return vendor, date, amount

