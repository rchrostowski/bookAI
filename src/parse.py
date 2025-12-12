from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional, Tuple

# Words we prefer for "final total"
TOTAL_LABELS = [
    "TOTAL",
    "AMOUNT DUE",
    "BALANCE DUE",
    "TOTAL DUE",
    "GRAND TOTAL",
    "TOTAL SALE",
]

# Words we should NOT treat as final total
AVOID_TOTAL_WORDS = [
    "SUBTOTAL",
    "SUB TOTAL",
    "TAX",
    "SALES TAX",
    "TIP",
    "GRATUITY",
    "CHANGE",
    "CASH",
    "VISA",
    "MASTERCARD",
    "AMEX",
    "DISC",
    "DISCOUNT",
]

# Common "junk" header/footer lines for vendor detection
VENDOR_JUNK = [
    "THANK YOU",
    "THANKS",
    "WELCOME",
    "CUSTOMER COPY",
    "MERCHANT COPY",
    "APPROVED",
    "DECLINED",
    "PLEASE COME AGAIN",
    "COPY",
    "RECEIPT",
    "INVOICE",
]


def extract_fields(raw_text: str) -> Dict:
    """
    Extract vendor, date (YYYY-MM-DD), and amount (float) from OCR text.
    Returns dict with keys: vendor, date, amount.
    """
    text = raw_text or ""
    lines = _clean_lines(text)

    vendor = _extract_vendor(lines)
    date = _extract_date(text, lines)
    amount = _extract_total_amount(text, lines)

    return {
        "vendor": vendor,
        "date": date,
        "amount": amount,
    }


# -----------------------------
# Helpers
# -----------------------------

def _clean_lines(text: str) -> list[str]:
    # Normalize whitespace, split lines, remove empties
    raw_lines = [l.strip() for l in (text or "").splitlines()]
    raw_lines = [re.sub(r"\s+", " ", l) for l in raw_lines]
    raw_lines = [l for l in raw_lines if l]

    # Remove super-short noise lines
    cleaned = []
    for l in raw_lines:
        if len(l) <= 1:
            continue
        cleaned.append(l)
    return cleaned


def _extract_vendor(lines: list[str]) -> str:
    """
    Heuristic: vendor is usually in first few lines, often uppercase, not an address,
    not a phone, not a 'thank you' line.
    """
    # Look at top N lines
    top = lines[:12]

    candidates = []
    for l in top:
        u = l.upper()

        # Skip obvious junk
        if any(j in u for j in VENDOR_JUNK):
            continue

        # Skip lines that look like dates, phone numbers, or addresses
        if _looks_like_phone(u):
            continue
        if _looks_like_address(u):
            continue
        if _looks_like_date_line(u):
            continue

        # Too numeric?
        digit_ratio = sum(ch.isdigit() for ch in u) / max(1, len(u))
        if digit_ratio > 0.35:
            continue

        # Prefer more "name-like" lines
        score = 0
        if u == l:  # already uppercase
            score += 2
        if 4 <= len(l) <= 32:
            score += 2
        if re.search(r"[A-Z]", u):
            score += 1
        if re.search(r"(LLC|INC|CORP|CO\.|COMPANY|STORE|MARKET|GAS|STATION|SUPPLY|HARDWARE|CAFE|COFFEE|BAR|GRILL)", u):
            score += 1

        candidates.append((score, l))

    if not candidates:
        return ""

    candidates.sort(key=lambda x: x[0], reverse=True)
    return _normalize_vendor(candidates[0][1])


def _normalize_vendor(v: str) -> str:
    v = (v or "").strip()
    # fix OCR artifacts like "H0ME" -> "HOME" (optional light cleanup)
    v = v.replace("0", "O") if v.isupper() else v
    return v


def _extract_date(text: str, lines: list[str]) -> str:
    """
    Extracts the most plausible receipt date and returns YYYY-MM-DD.
    Handles:
      - 03/14/2025, 3/14/25
      - 2025-03-14
      - 03-14-2025
      - Mar 14 2025 / March 14, 2025
    Prefers dates that appear near top of receipt.
    """
    # prioritize top area
    top_text = "\n".join(lines[:20])

    # Try multiple patterns
    candidates = []
    for src, weight in [(top_text, 2.0), (text, 1.0)]:
        for dt, span in _find_dates(src):
            candidates.append((weight, dt))

    if not candidates:
        return ""

    # Choose highest weight (top region preferred), most recent-ish if ties
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = candidates[0][1]
    return best.strftime("%Y-%m-%d")


def _find_dates(s: str) -> list[Tuple[datetime, Tuple[int, int]]]:
    out: list[Tuple[datetime, Tuple[int, int]]] = []

    if not s:
        return out

    # 1) YYYY-MM-DD
    for m in re.finditer(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b", s):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dt = _safe_date(y, mo, d)
        if dt:
            out.append((dt, m.span()))

    # 2) MM/DD/YYYY or MM/DD/YY or MM-DD-YYYY
    for m in re.finditer(r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-](\d{2}|\d{4})\b", s):
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y = 2000 + y  # assume 20xx
        dt = _safe_date(y, mo, d)
        if dt:
            out.append((dt, m.span()))

    # 3) Month name formats: Mar 14 2025 / March 14, 2025
    month_map = {
        "JAN": 1, "JANUARY": 1,
        "FEB": 2, "FEBRUARY": 2,
        "MAR": 3, "MARCH": 3,
        "APR": 4, "APRIL": 4,
        "MAY": 5,
        "JUN": 6, "JUNE": 6,
        "JUL": 7, "JULY": 7,
        "AUG": 8, "AUGUST": 8,
        "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
        "OCT": 10, "OCTOBER": 10,
        "NOV": 11, "NOVEMBER": 11,
        "DEC": 12, "DECEMBER": 12,
    }
    for m in re.finditer(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:,)?\s+(20\d{2})\b", s):
        mon_txt = m.group(1).upper()
        mon_txt = mon_txt[:3] if mon_txt[:3] in month_map else mon_txt
        mo = month_map.get(mon_txt, None) or month_map.get(m.group(1).upper(), None)
        if not mo:
            continue
        d = int(m.group(2))
        y = int(m.group(3))
        dt = _safe_date(y, mo, d)
        if dt:
            out.append((dt, m.span()))

    return out


def _safe_date(y: int, m: int, d: int) -> Optional[datetime]:
    try:
        return datetime(y, m, d)
    except Exception:
        return None


def _extract_total_amount(text: str, lines: list[str]) -> float:
    """
    Extract total using strong heuristics:
    - Prefer lines with TOTAL-like labels, excluding SUBTOTAL/TAX/TIP/etc.
    - If multiple candidates, choose the largest amount near a total label
    - Fallback: choose the largest plausible amount in the whole receipt
    """
    # Parse candidates from lines with labels
    labeled_candidates = []

    for i, line in enumerate(lines):
        u = line.upper()

        # Must contain any total label
        if not any(lbl in u for lbl in TOTAL_LABELS):
            continue

        # Must NOT contain avoid words
        if any(bad in u for bad in AVOID_TOTAL_WORDS):
            continue

        # Find amounts in this line and nearby lines
        near = [line]
        if i + 1 < len(lines):
            near.append(lines[i + 1])
        if i - 1 >= 0:
            near.append(lines[i - 1])

        amounts = []
        for chunk in near:
            amounts.extend(_find_amounts(chunk))

        amounts = [a for a in amounts if _is_plausible_money(a)]
        if amounts:
            # prefer the max amount near TOTAL label
            labeled_candidates.append(max(amounts))

    if labeled_candidates:
        return float(max(labeled_candidates))

    # Fallback: look for lines that are probably totals without explicit label
    # (some receipts just have a big number at bottom)
    all_amounts = _find_amounts(text)
    all_amounts = [a for a in all_amounts if _is_plausible_money(a)]

    if not all_amounts:
        return 0.0

    # Prefer largest plausible amount
    return float(max(all_amounts))


def _find_amounts(s: str) -> list[float]:
    """
    Finds dollar amounts like:
      $47.62
      47.62
      1,234.56
    Avoids capturing years or long integers.
    """
    if not s:
        return []

    out = []
    # $ optional, commas optional
    for m in re.finditer(r"(?<!\d)(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2}))", s):
        raw = m.group(1)
        raw = raw.replace("$", "").replace(",", "").strip()
        try:
            out.append(float(raw))
        except Exception:
            pass
    return out


def _is_plausible_money(x: float) -> bool:
    # Adjust bounds if you want, but this avoids grabbing tiny tax lines etc.
    return 0.50 <= x <= 20000.0


def _looks_like_phone(s: str) -> bool:
    return bool(re.search(r"\b(\+?1[-\s]?)?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{4}\b", s))


def _looks_like_address(s: str) -> bool:
    # very loose heuristic
    return bool(re.search(r"\b(ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|DR|DRIVE|LN|LANE|HWY|HIGHWAY)\b", s))


def _looks_like_date_line(s: str) -> bool:
    return bool(re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b", s)) or bool(
        re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-](\d{2}|\d{4}))\b", s)
    )


