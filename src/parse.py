from __future__ import annotations

import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional


# -----------------------------
# Patterns
# -----------------------------
PHONE_RE = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(-\d{4})?\b")
DATE_YMD_RE = re.compile(r"\b(20\d{2})[-/](\d{2})[-/](\d{2})\b")
DATE_MDY_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})(?::\d{2})?\b", re.I)

# Money:
# - Keep "normal dollars" (2 decimals)
# - Also allow integer dollars (rare but happens)
MONEY_2DP_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{2}))\b")
MONEY_INT_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,6}(?:,[0-9]{3})*)\b")

# Keywords for totals (expanded)
TOTAL_KEYS_STRONG = [
    "grand total",
    "amount due",
    "total due",
    "balance due",
    "amount",
    "amount paid",
    "total sale",
    "sale total",
    "total amount",
    "order total",
    "invoice total",
    "total",
]
TOTAL_KEYS_WEAK = ["due", "balance", "amt", "paid"]

EXCLUDE_AMOUNT_KEYS = [
    "subtotal", "sub total", "tax", "sales tax", "vat", "discount", "coupon",
    "change", "cash", "tender", "tip", "gratuity", "service charge",
    "auth", "approval", "authorized", "ref", "trans", "transaction",
    "debit", "credit", "visa", "mastercard", "amex", "discover",
]

# Unit traps (gas/weights/quantities) that often include 2-decimal numbers
UNIT_TRAPS = [
    "gal", "gallon", "gallons", "ltr", "liter", "liters", "l ",
    "qty", "quantity", "units", "unit",
    "lbs", "lb", "pounds", "oz", "ounces",
    "volume", "vol", "pump",
    "price/gal", "price per gal", "ppg", "@",
]

# Vendor noise
VENDOR_NOISE = {
    "thank you", "thanks", "welcome", "come again",
    "receipt", "invoice", "customer copy", "merchant copy", "copy",
    "total", "subtotal", "tax", "change", "cash", "visa", "mastercard", "amex", "discover",
    "approved", "authorization", "auth", "ref", "transaction", "trans",
}

ADDRESS_HINTS = {
    "st", "street", "rd", "road", "ave", "avenue", "blvd", "boulevard", "ln", "lane",
    "dr", "drive", "hwy", "highway", "suite", "ste", "unit",
    "pa", "nj", "ny", "ca", "tx", "fl", "il", "oh", "wa", "va", "md", "ma", "ct",
}


# -----------------------------
# Helpers
# -----------------------------
def _clean_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 $./@]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _looks_like_address(line: str) -> bool:
    up = line.upper()
    if STATE_ZIP_RE.search(up):
        return True
    low = _norm(line)
    toks = set(low.split())
    if len(toks & ADDRESS_HINTS) >= 1 and any(ch.isdigit() for ch in line):
        return True
    if re.search(r"^\d{1,6}\s+[A-Za-z]", line):  # street number + word
        return True
    return False

def _has_date_or_time(line: str) -> bool:
    return bool(DATE_YMD_RE.search(line) or DATE_MDY_RE.search(line) or TIME_RE.search(line))

def _looks_like_vendor_noise(line: str) -> bool:
    low = _norm(line)
    if not low:
        return True
    if any(k in low for k in VENDOR_NOISE):
        return True
    if PHONE_RE.search(line):
        return True
    if _has_date_or_time(line):
        return True
    if _looks_like_address(line):
        return True
    # very long lines are often policy text
    if len(line) > 44:
        return True
    return False

def _collapse_spaced_letters(s: str) -> str:
    toks = s.split()
    if len(toks) >= 4 and all(len(t) == 1 for t in toks[:4]):
        return "".join(toks)
    return s

def _parse_money_val(token: str) -> Optional[float]:
    try:
        token = token.replace(",", "").strip()
        val = float(token)
        if val > 99999:
            return None
        return val
    except Exception:
        return None

def _line_has_currency_marker(line: str) -> bool:
    # Strong signal that a number is a dollar amount, not gallons/qty
    l = line or ""
    low = _norm(l)
    return ("$" in l) or (" usd" in f" {low} ") or ("us$" in low)

def _line_has_unit_trap(line: str) -> bool:
    low = f" {_norm(line)} "
    return any(f" {u} " in low or u in low for u in UNIT_TRAPS)


# -----------------------------
# Vendor extraction
# -----------------------------
def _vendor_score(line: str) -> float:
    line = _clean_line(line)
    if _looks_like_vendor_noise(line):
        return -999.0

    score = 0.0

    letters = sum(1 for c in line if c.isalpha())
    uppers = sum(1 for c in line if c.isalpha() and c.isupper())
    if letters > 0:
        score += 0.9 * (uppers / letters)

    digits = sum(1 for c in line if c.isdigit())
    punct = sum(1 for c in line if (not c.isalnum() and c != " "))
    score -= 0.18 * digits
    score -= 0.22 * punct

    w = line.split()
    if 1 <= len(w) <= 5:
        score += 0.6
    else:
        score -= 0.3

    low = _norm(line)
    if any(k in low for k in ["inc", "llc", "corp", "co", "company", "market", "store", "diner", "cafe", "coffee", "gas", "station"]):
        score += 0.25

    return score

def extract_vendor(raw_text: str) -> Tuple[str, float, List[str]]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    top = lines[:22]

    candidates: List[str] = []
    for i, a in enumerate(top):
        if a:
            candidates.append(a)
        if i + 1 < len(top):
            b = top[i + 1]
            if 3 <= len(a) <= 22 and 3 <= len(b) <= 26:
                if not _looks_like_vendor_noise(a) and not _looks_like_vendor_noise(b):
                    candidates.append(f"{a} {b}")

    scored = sorted(((c, _vendor_score(c)) for c in candidates), key=lambda x: x[1], reverse=True)
    if not scored or scored[0][1] < -100:
        return ("", 0.0, [])

    best = _collapse_spaced_letters(scored[0][0]).strip()

    seen = set()
    cand_list = []
    for c, s in scored[:10]:
        c2 = _collapse_spaced_letters(c).strip()
        k = _norm(c2)
        if k and k not in seen and s > -50:
            seen.add(k)
            cand_list.append(c2)
        if len(cand_list) >= 3:
            break

    conf = max(0.0, min(0.92, (scored[0][1] + 0.1)))
    conf = float(max(0.25, conf))

    return (best, conf, cand_list)


# -----------------------------
# Date extraction
# -----------------------------
def extract_date(raw_text: str) -> Tuple[str, float]:
    text = raw_text or ""

    m = DATE_YMD_RE.search(text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return (dt.strftime("%Y-%m-%d"), 0.95)
        except Exception:
            pass

    for m in DATE_MDY_RE.finditer(text):
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        try:
            dt = datetime(int(yy), int(mm), int(dd))
            if 2010 <= dt.year <= 2035:
                return (dt.strftime("%Y-%m-%d"), 0.85)
        except Exception:
            continue

    return ("", 0.0)


# -----------------------------
# Amount extraction (REDONE)
# -----------------------------
def _extract_money_spans(line: str) -> List[Tuple[float, int, int, str]]:
    """
    Returns list of (value, start, end, raw_match_text).
    Prefers 2-decimal matches; falls back to integers only if needed.
    """
    spans: List[Tuple[float, int, int, str]] = []

    for m in MONEY_2DP_RE.finditer(line or ""):
        raw = m.group(0)
        tok = m.group(1)
        v = _parse_money_val(tok)
        if v is not None:
            spans.append((float(v), m.start(), m.end(), raw))

    # If no 2dp values at all, consider integers (rare receipts)
    if not spans:
        for m in MONEY_INT_RE.finditer(line or ""):
            raw = m.group(0)
            tok = m.group(1)
            # avoid years / long ids
            if len(tok.replace(",", "")) >= 5:
                continue
            v = _parse_money_val(tok)
            if v is not None:
                spans.append((float(v), m.start(), m.end(), raw))

    return spans

def _contains_any_key(norm_line: str, keys: List[str]) -> bool:
    return any(k in norm_line for k in keys)

def _is_excluded_amount_line(line: str) -> bool:
    low = _norm(line)
    return any(k in low for k in EXCLUDE_AMOUNT_KEYS)

def _pick_best_value_from_line(line: str) -> Optional[float]:
    """
    Prefer the *rightmost* monetary value on the line (totals are usually last),
    and strongly prefer values that include a currency marker somewhere on the line.
    """
    spans = _extract_money_spans(line)
    if not spans:
        return None

    # If we have multiple values, totals are commonly the last number on the line.
    # Example: "TOTAL 1.23 4.56 38.72" -> pick 38.72
    spans_sorted = sorted(spans, key=lambda x: (x[1], x[2]))
    return float(spans_sorted[-1][0])

def extract_amount(raw_text: str) -> Tuple[float, float]:
    """
    More foolproof strategy (fixes gas receipts showing gallons/qty as "total"):

    1) Build scored candidate lines from the *bottom half* of the receipt.
       - Strongly favor lines containing TOTAL/AMOUNT/SALE TOTAL
       - Strongly favor lines with a currency marker ($/USD)
       - Strongly penalize unit-trap lines (gallons, qty, lbs, etc.) unless they also look like currency
       - Exclude subtotal/tax/tip/change/etc.

    2) Take the best line, then choose the *rightmost* money amount on that line.
       (Not max(), because max() can pick the wrong thing on weird lines.)
    """
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return (0.0, 0.0)

    n = len(lines)
    bottom_start = int(n * 0.50) if n >= 10 else 0
    search_lines = lines[bottom_start:]

    candidates: List[Tuple[float, float, str]] = []  # (score, value, line)
    for idx, ln in enumerate(search_lines):
        low = _norm(ln)
        if not low:
            continue
        if _is_excluded_amount_line(ln):
            continue

        # must have some money-like number to consider
        val = _pick_best_value_from_line(ln)
        if val is None:
            continue

        score = 0.0

        # Position bonus: closer to bottom = more likely
        # idx goes 0..len(search_lines)-1
        if len(search_lines) > 1:
            score += 0.40 * (idx / (len(search_lines) - 1))

        # Keyword bonuses
        if _contains_any_key(low, TOTAL_KEYS_STRONG):
            score += 1.30
        elif _contains_any_key(low, TOTAL_KEYS_WEAK):
            score += 0.55

        # Currency marker bonus (this is the big fix for gallons/qty totals)
        has_currency = _line_has_currency_marker(ln)
        if has_currency:
            score += 1.10
        else:
            score -= 0.25  # mild penalty; still allow if it's the only line

        # Unit-trap penalty: "TOTAL GALLONS 38.72" should NOT win
        if _line_has_unit_trap(ln):
            # Only forgive if line clearly looks like currency
            score -= (1.40 if not has_currency else 0.40)

        # Extra nudge if line explicitly says "amount" or "grand total"
        if "grand total" in low:
            score += 0.50
        if "amount" in low:
            score += 0.35

        # Penalize lines that look like they are describing unit price math
        # (common in fuel: "$3.199/gal" + other numbers)
        if "/gal" in low or "per gal" in low or "price/gal" in low:
            score -= 0.25

        candidates.append((score, float(val), ln))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_val, _best_line = candidates[0]

        # Confidence mapping
        # If we had currency + strong key near bottom: very high
        conf = 0.65
        if best_score >= 2.2:
            conf = 0.95
        elif best_score >= 1.6:
            conf = 0.88
        elif best_score >= 1.1:
            conf = 0.78
        else:
            conf = 0.68

        return (float(best_val), float(conf))

    # Fallback: pick max *dollar-looking* value from bottom, but avoid unit-trap lines first
    fallback_vals: List[float] = []
    for ln in search_lines:
        if _is_excluded_amount_line(ln):
            continue
        if _line_has_unit_trap(ln) and not _line_has_currency_marker(ln):
            continue
        for v, *_ in _extract_money_spans(ln):
            fallback_vals.append(float(v))
    if fallback_vals:
        return (float(max(fallback_vals)), 0.60)

    # Last resort: any value anywhere
    all_vals: List[float] = []
    for ln in lines:
        if _is_excluded_amount_line(ln):
            continue
        for v, *_ in _extract_money_spans(ln):
            all_vals.append(float(v))
    if all_vals:
        return (float(max(all_vals)), 0.50)

    return (0.0, 0.0)


# -----------------------------
# Public API: extract_fields
# -----------------------------
def extract_fields(raw_text: str) -> Dict:
    """
    Output stays backward compatible with your app:
      {"vendor": str, "date": str, "amount": float}

    Extras you can optionally use:
      vendor_candidates, vendor_confidence, date_confidence, amount_confidence, parse_confidence
    """
    vendor, vconf, vcands = extract_vendor(raw_text)
    date, dconf = extract_date(raw_text)
    amount, acon = extract_amount(raw_text)

    parse_conf = 0.0
    parse_conf += 0.45 * vconf
    parse_conf += 0.35 * acon
    parse_conf += 0.20 * dconf

    return {
        "vendor": vendor,
        "date": date,
        "amount": float(amount),
        "vendor_candidates": vcands,
        "vendor_confidence": float(vconf),
        "date_confidence": float(dconf),
        "amount_confidence": float(acon),
        "parse_confidence": float(round(parse_conf, 3)),
    }


