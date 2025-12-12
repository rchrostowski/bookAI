from __future__ import annotations

import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional


# -----------------------------
# Patterns
# -----------------------------
PHONE_RE = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(-\d{4})?\b")

DATE_YMD_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
DATE_MDY_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
DATE_DOT_RE = re.compile(r"\b(\d{1,2})[.](\d{1,2})[.](\d{2,4})\b")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})(?::\d{2})?\s*([AP]M)?\b", re.I)

MONTH_NAME_RE = re.compile(
    r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.I,
)

# Money: prefer 2 decimals; allow integers as fallback
MONEY_2DP_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{2}))\b")
MONEY_INT_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,6}(?:,[0-9]{3})*)\b")

# Total keywords
TOTAL_KEYS_STRONG = [
    "grand total", "amount due", "total due", "balance due",
    "total amount", "order total", "invoice total",
    "total", "amount paid", "sale total", "total sale",
]
TOTAL_KEYS_WEAK = ["due", "balance", "amt", "paid"]

# Things we should NOT treat as totals
EXCLUDE_AMOUNT_KEYS = [
    "subtotal", "sub total", "tax", "sales tax", "vat", "discount", "coupon",
    "change", "cash", "tender", "tip", "gratuity", "service charge",
    "auth", "approval", "authorized", "ref", "trans", "transaction",
    "debit", "credit", "visa", "mastercard", "mc", "amex", "discover",
    "card#", "card #", "expdate", "entry method",
]

# Unit traps (gas/qty/etc.)
UNIT_TRAPS = [
    "gal", "gallon", "gallons", "ltr", "liter", "liters",
    "qty", "quantity", "units", "unit",
    "lbs", "lb", "pounds", "oz", "ounces",
    "volume", "vol", "pump",
    "price/gal", "price per gal", "ppg", "/gal", "per gal", "@",
]

# Vendor noise tokens
VENDOR_NOISE = {
    "thank you", "thanks", "welcome", "come again",
    "receipt", "invoice", "customer copy", "merchant copy", "copy",
    "subtotal", "tax", "change", "cash",
    "approved", "authorization", "auth", "ref", "transaction", "trans",
    "visit", "returns", "return policy", "refund", "policy", "www", "http",
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
    s = re.sub(r"[^a-z0-9 $./@#&]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
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

def _looks_like_address(line: str) -> bool:
    up = line.upper()
    if STATE_ZIP_RE.search(up):
        return True
    low = _norm(line)
    toks = set(low.split())
    if len(toks & ADDRESS_HINTS) >= 1 and any(ch.isdigit() for ch in line):
        return True
    if re.search(r"^\d{1,6}\s+[A-Za-z]", line):
        return True
    return False

def _has_date_or_time(line: str) -> bool:
    t = line or ""
    return bool(DATE_YMD_RE.search(t) or DATE_MDY_RE.search(t) or DATE_DOT_RE.search(t) or MONTH_NAME_RE.search(t) or TIME_RE.search(t))

def _line_has_unit_trap(line: str) -> bool:
    low = f" {_norm(line)} "
    return any(u in low for u in UNIT_TRAPS)

def _line_has_currency_marker(line: str) -> bool:
    l = line or ""
    low = _norm(l)
    return ("$" in l) or (" usd" in f" {low} ") or ("us$" in low)

def _collapse_spaced_letters(s: str) -> str:
    toks = s.split()
    if len(toks) >= 4 and all(len(t) == 1 for t in toks[:4]):
        return "".join(toks)
    return s

def _looks_like_vendor_noise(line: str) -> bool:
    low = _norm(line)
    if not low:
        return True

    # Specific "policy / footer" style lines
    if any(k in low for k in VENDOR_NOISE):
        return True

    if PHONE_RE.search(line):
        return True
    if _has_date_or_time(line):
        return True
    if _looks_like_address(line):
        return True

    # If it's extremely long, it’s probably not vendor (but allow normal long store headers)
    if len(line) > 90:
        return True

    return False


# -----------------------------
# Vendor extraction
# -----------------------------
def _vendor_score(line: str, idx: int) -> float:
    line = _clean_line(line)
    low = _norm(line)

    if _looks_like_vendor_noise(line):
        return -999.0

    score = 0.0

    # Position bonus: earlier lines more likely vendor
    # idx is within "top slice" (0 = first line)
    score += max(0.0, 0.9 - 0.08 * idx)

    # Uppercase/letters ratio helps with headers
    letters = sum(1 for c in line if c.isalpha())
    uppers = sum(1 for c in line if c.isalpha() and c.isupper())
    if letters > 0:
        score += 0.9 * (uppers / letters)

    # Penalize digits, but NOT too hard (stores often have #2259)
    digits = sum(1 for c in line if c.isdigit())
    score -= 0.10 * digits

    # Prefer 1–7 words
    w = line.split()
    if 1 <= len(w) <= 7:
        score += 0.6
    else:
        score -= 0.2

    # Boost common merchant words
    merchant_hits = ["inc", "llc", "corp", "co", "company", "market", "store", "books", "book", "cafe", "coffee", "gas", "station", "pharmacy", "restaurant"]
    if any(k in low for k in merchant_hits):
        score += 0.35

    # Penalize footer-ish wording hard
    footer_hits = ["visit", "returns", "refund", "policy", "customer copy", "merchant copy"]
    if any(k in low for k in footer_hits):
        score -= 1.2

    # If line is mostly weird short tokens, penalize (helps kill "MO. Wii HT")
    toks = [t for t in re.split(r"\s+", line) if t]
    short = sum(1 for t in toks if len(t) <= 2)
    if toks and (short / len(toks)) >= 0.6:
        score -= 0.8

    return score

def extract_vendor(raw_text: str) -> Tuple[str, float, List[str]]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    top = lines[:28]  # slightly larger window

    candidates: List[str] = []
    for i, a in enumerate(top):
        if a:
            candidates.append(a)
        if i + 1 < len(top):
            b = top[i + 1]
            if 3 <= len(a) <= 40 and 3 <= len(b) <= 40:
                if not _looks_like_vendor_noise(a) and not _looks_like_vendor_noise(b):
                    candidates.append(f"{a} {b}")

    scored = []
    for c in candidates:
        # score using original index if possible (approx by finding in top)
        try:
            idx = top.index(c)
        except Exception:
            idx = 8
        scored.append((c, _vendor_score(c, idx)))

    scored.sort(key=lambda x: x[1], reverse=True)

    if not scored or scored[0][1] < -100:
        return ("", 0.0, [])

    best = _collapse_spaced_letters(scored[0][0]).strip()

    # Candidate list (top 3 unique)
    seen = set()
    cand_list = []
    for c, s in scored[:12]:
        c2 = _collapse_spaced_letters(c).strip()
        k = _norm(c2)
        if k and k not in seen and s > -50:
            seen.add(k)
            cand_list.append(c2)
        if len(cand_list) >= 3:
            break

    # Confidence (bounded)
    # Typical good scores land ~1.5–2.8 after bonuses; map into 0.25..0.92
    raw = scored[0][1]
    conf = 0.25
    if raw >= 2.4:
        conf = 0.92
    elif raw >= 1.8:
        conf = 0.85
    elif raw >= 1.2:
        conf = 0.72
    else:
        conf = 0.55

    return (best, float(conf), cand_list)


# -----------------------------
# Date extraction
# -----------------------------
def _try_make_date(yy: int, mm: int, dd: int) -> Optional[str]:
    try:
        dt = datetime(int(yy), int(mm), int(dd))
        if 2010 <= dt.year <= 2035:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        return None
    return None

def extract_date(raw_text: str) -> Tuple[str, float]:
    text = raw_text or ""

    # YYYY-MM-DD first
    m = DATE_YMD_RE.search(text)
    if m:
        d = _try_make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return (d, 0.95)

    # MM/DD/YYYY
    for m in DATE_MDY_RE.finditer(text):
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        d = _try_make_date(int(yy), int(mm), int(dd))
        if d:
            return (d, 0.85)

    # MM.DD.YYYY
    for m in DATE_DOT_RE.finditer(text):
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        d = _try_make_date(int(yy), int(mm), int(dd))
        if d:
            return (d, 0.80)

    # Month name (Jan 8, 2025)
    m = MONTH_NAME_RE.search(text)
    if m:
        mon_txt = m.group(1).lower()
        mon_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12}
        mm = mon_map.get(mon_txt[:4], mon_map.get(mon_txt[:3]))
        dd = int(m.group(2))
        yy = int(m.group(3))
        if mm:
            d = _try_make_date(yy, mm, dd)
            if d:
                return (d, 0.82)

    return ("", 0.0)


# -----------------------------
# Amount extraction
# -----------------------------
def _extract_money_spans(line: str) -> List[Tuple[float, int, int, str]]:
    spans: List[Tuple[float, int, int, str]] = []
    for m in MONEY_2DP_RE.finditer(line or ""):
        raw = m.group(0)
        tok = m.group(1)
        v = _parse_money_val(tok)
        if v is not None:
            spans.append((float(v), m.start(), m.end(), raw))

    if not spans:
        for m in MONEY_INT_RE.finditer(line or ""):
            raw = m.group(0)
            tok = m.group(1)
            if len(tok.replace(",", "")) >= 5:
                continue
            v = _parse_money_val(tok)
            if v is not None:
                spans.append((float(v), m.start(), m.end(), raw))

    return spans

def _is_excluded_amount_line(line: str) -> bool:
    low = _norm(line)
    return any(k in low for k in EXCLUDE_AMOUNT_KEYS)

def _contains_any_key(norm_line: str, keys: List[str]) -> bool:
    return any(k in norm_line for k in keys)

def _pick_rightmost_value(line: str) -> Optional[float]:
    spans = _extract_money_spans(line)
    if not spans:
        return None
    spans.sort(key=lambda x: (x[1], x[2]))
    return float(spans[-1][0])

def extract_amount(raw_text: str) -> Tuple[float, float]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return (0.0, 0.0)

    n = len(lines)
    bottom_start = int(n * 0.45) if n >= 10 else 0
    search_lines = lines[bottom_start:]

    candidates: List[Tuple[float, float, str]] = []  # (score, value, line)

    for idx, ln in enumerate(search_lines):
        low = _norm(ln)
        if not low:
            continue

        # Must have at least one money-like number
        val = _pick_rightmost_value(ln)
        if val is None:
            continue

        # Exclude tender/payment lines etc.
        if _is_excluded_amount_line(ln):
            continue

        score = 0.0

        # closer to bottom = better
        if len(search_lines) > 1:
            score += 0.40 * (idx / (len(search_lines) - 1))

        # Strong “total” wins hard
        if _contains_any_key(low, TOTAL_KEYS_STRONG):
            score += 1.50
        elif _contains_any_key(low, TOTAL_KEYS_WEAK):
            score += 0.50

        # Penalize unit traps (gallons/qty)
        if _line_has_unit_trap(ln):
            score -= 1.30

        # Currency marker helps but not required (some receipts print TOTAL without $)
        if _line_has_currency_marker(ln):
            score += 0.40

        candidates.append((score, float(val), ln))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_val, _ = candidates[0]

        if best_score >= 2.0:
            conf = 0.95
        elif best_score >= 1.4:
            conf = 0.88
        elif best_score >= 0.9:
            conf = 0.78
        else:
            conf = 0.68

        return (float(best_val), float(conf))

    # fallback: max value in bottom half excluding unit trap lines
    vals = []
    for ln in search_lines:
        if _is_excluded_amount_line(ln):
            continue
        if _line_has_unit_trap(ln):
            continue
        for v, *_ in _extract_money_spans(ln):
            vals.append(float(v))
    if vals:
        return (float(max(vals)), 0.55)

    return (0.0, 0.0)


# -----------------------------
# Public API
# -----------------------------
def extract_fields(raw_text: str) -> Dict:
    vendor, vconf, vcands = extract_vendor(raw_text)
    date, dconf = extract_date(raw_text)
    amount, acon = extract_amount(raw_text)

    parse_conf = 0.0
    parse_conf += 0.45 * float(vconf)
    parse_conf += 0.35 * float(acon)
    parse_conf += 0.20 * float(dconf)

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



