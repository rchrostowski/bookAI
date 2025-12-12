from __future__ import annotations

import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional


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

# Money: support "30.74" and "30,74"
MONEY_ANY_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:[.,][0-9]{2})?)\b")

TOTAL_KEYS_STRONG = [
    "grand total", "amount due", "total due", "balance due",
    "total amount", "order total", "invoice total",
    "total", "amount paid", "sale total", "total sale", "sale:",
]
TOTAL_KEYS_WEAK = ["due", "balance", "amt", "paid"]

EXCLUDE_AMOUNT_KEYS = [
    "subtotal", "sub total", "tax", "sales tax", "vat", "discount", "coupon",
    "change", "cash", "tender", "tip", "gratuity", "service charge",
    "auth", "approval", "authorized", "ref", "trans", "transaction",
    # payment lines are NOT totals
    "debit", "credit", "visa", "mastercard", "mc", "amex", "discover",
    "card#", "card #", "expdate", "entry method", "auth:",
]

UNIT_TRAPS = [
    "gal", "gallon", "gallons", "ltr", "liter", "liters",
    "qty", "quantity", "units", "unit",
    "lbs", "lb", "pounds", "oz", "ounces",
    "volume", "vol", "pump",
    "price/gal", "price per gal", "ppg", "/gal", "per gal", "@",
]

VENDOR_NOISE = {
    "thank you", "thanks", "welcome", "come again",
    "receipt", "invoice", "customer copy", "merchant copy", "copy",
    "subtotal", "tax", "change", "cash",
    "approved", "authorization", "auth", "ref", "transaction", "trans",
    "visit", "returns", "return policy", "refund", "policy", "www", "http",
    "customer", "copy",
}

ADDRESS_HINTS = {
    "st", "street", "rd", "road", "ave", "avenue", "blvd", "boulevard", "ln", "lane",
    "dr", "drive", "hwy", "highway", "suite", "ste", "unit",
    "pa", "nj", "ny", "ca", "tx", "fl", "il", "oh", "wa", "va", "md", "ma", "ct",
}

MERCHANT_BOOST = [
    # generic
    "inc", "llc", "corp", "company", "co", "store", "market",
    "restaurant", "cafe", "coffee", "diner", "grill", "bar",
    "gas", "station", "pharmacy", "hardware", "auto",
    # receipts often
    "books", "book", "bookseller", "bookstore",
]


def _clean_line(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 #&/.:@,$-]+", " ", s)
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
    if re.search(r"^\d{1,6}\s+[A-Za-z]", line):
        return True
    return False

def _has_date_or_time(line: str) -> bool:
    t = line or ""
    return bool(
        DATE_YMD_RE.search(t) or DATE_MDY_RE.search(t) or DATE_DOT_RE.search(t)
        or MONTH_NAME_RE.search(t) or TIME_RE.search(t)
    )

def _parse_money_val(token: str) -> Optional[float]:
    """
    Accept:
      30.74
      30,74  (comma decimal)
      1,234.56
    """
    try:
        tok = token.strip()
        # If it's comma-decimal (xx,yy) and no dot present, treat comma as decimal point.
        if "," in tok and "." not in tok:
            # Example: "30,74" -> "30.74"
            if re.search(r"\d+,\d{2}$", tok):
                tok = tok.replace(",", ".")
            else:
                tok = tok.replace(",", "")  # thousands separators only
        else:
            tok = tok.replace(",", "")  # remove thousands separators

        val = float(tok)
        if val > 99999:
            return None
        return val
    except Exception:
        return None

def _line_amounts(line: str) -> List[float]:
    vals = []
    for tok in MONEY_ANY_RE.findall(line or ""):
        v = _parse_money_val(tok)
        if v is not None:
            vals.append(v)
    return vals

def _line_has_unit_trap(line: str) -> bool:
    low = f" {_norm(line)} "
    return any(u in low for u in UNIT_TRAPS)

def _is_excluded_amount_line(line: str) -> bool:
    low = _norm(line)
    return any(k in low for k in EXCLUDE_AMOUNT_KEYS)

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
    # extremely long lines are usually policy paragraphs
    if len(line) > 110:
        return True
    return False

def _is_garbage_vendor(line: str) -> bool:
    """
    Kill OCR junk like: 'SPP', 'ote', 'PREY', random short tokens.
    """
    s = (line or "").strip()
    if not s:
        return True

    # too short and no spaces
    if len(s) <= 4 and " " not in s:
        return True

    # needs some real letters
    letters = sum(ch.isalpha() for ch in s)
    if letters < 4:
        return True

    # if mostly tiny tokens (<=2 chars), it's usually junk
    toks = [t for t in re.split(r"\s+", s) if t]
    if toks:
        short = sum(1 for t in toks if len(t) <= 2)
        if (short / len(toks)) >= 0.75:
            return True

    return False

def _collapse_spaced_letters(s: str) -> str:
    toks = s.split()
    if len(toks) >= 4 and all(len(t) == 1 for t in toks[:4]):
        return "".join(toks)
    return s


# -----------------------------
# Vendor
# -----------------------------
def _vendor_score(line: str, idx: int) -> float:
    line = _clean_line(line)
    low = _norm(line)

    if _looks_like_vendor_noise(line):
        return -999.0
    if _is_garbage_vendor(line):
        return -999.0

    score = 0.0

    # Position bonus: earlier better, but NOT absolute (real vendor might be line ~10–25)
    score += max(0.0, 1.2 - 0.045 * idx)

    # Text quality: reward letters, mild penalty digits
    letters = sum(1 for c in line if c.isalpha())
    digits = sum(1 for c in line if c.isdigit())
    score += 0.06 * letters
    score -= 0.06 * digits

    # Merchant keyword boost
    if any(k in low for k in MERCHANT_BOOST):
        score += 1.2

    # Big boost if it looks like a store header with # or &
    if "#" in line or "&" in line:
        score += 0.6

    # Penalize obvious footer/policy lines
    if any(k in low for k in ["visit", "returns", "refund", "policy", "customer copy"]):
        score -= 2.0

    # Prefer 1–10 words
    w = line.split()
    if 1 <= len(w) <= 10:
        score += 0.5
    else:
        score -= 0.5

    return score

def extract_vendor(raw_text: str) -> Tuple[str, float, List[str]]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    # Look farther down because OCR sometimes spits garbage first
    top = lines[:70]

    candidates: List[Tuple[str, int]] = []
    for i, a in enumerate(top):
        if a:
            candidates.append((a, i))
        if i + 1 < len(top):
            b = top[i + 1]
            if 3 <= len(a) <= 60 and 3 <= len(b) <= 60:
                if not _looks_like_vendor_noise(a) and not _looks_like_vendor_noise(b):
                    candidates.append((f"{a} {b}", i))

    scored = sorted(
        ((c, _vendor_score(c, idx)) for c, idx in candidates),
        key=lambda x: x[1],
        reverse=True,
    )
    if not scored or scored[0][1] < -100:
        return ("", 0.0, [])

    best = _collapse_spaced_letters(scored[0][0]).strip()

    # Candidate list
    seen = set()
    cand_list = []
    for c, s in scored[:15]:
        c2 = _collapse_spaced_letters(c).strip()
        k = _norm(c2)
        if k and k not in seen and s > -50:
            seen.add(k)
            cand_list.append(c2)
        if len(cand_list) >= 3:
            break

    # Confidence: map score
    top_score = scored[0][1]
    if top_score >= 4.0:
        conf = 0.92
    elif top_score >= 3.0:
        conf = 0.85
    elif top_score >= 2.2:
        conf = 0.75
    else:
        conf = 0.60

    return (best, float(conf), cand_list)


# -----------------------------
# Date
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

    m = DATE_YMD_RE.search(text)
    if m:
        d = _try_make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return (d, 0.95)

    for m in DATE_MDY_RE.finditer(text):
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        d = _try_make_date(int(yy), int(mm), int(dd))
        if d:
            return (d, 0.85)

    for m in DATE_DOT_RE.finditer(text):
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        d = _try_make_date(int(yy), int(mm), int(dd))
        if d:
            return (d, 0.80)

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
# Amount
# -----------------------------
def extract_amount(raw_text: str) -> Tuple[float, float]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return (0.0, 0.0)

    n = len(lines)
    bottom_start = int(n * 0.45) if n >= 10 else 0
    bottom = lines[bottom_start:]

    # 1) Strong total lines
    for ln in reversed(bottom):
        low = _norm(ln)
        if _is_excluded_amount_line(ln):
            continue
        if _line_has_unit_trap(ln):
            continue
        if any(k in low for k in TOTAL_KEYS_STRONG):
            vals = _line_amounts(ln)
            if vals:
                return (float(max(vals)), 0.95)

    # 2) Any "total" line (not subtotal/tender)
    for ln in reversed(bottom):
        low = _norm(ln)
        if _is_excluded_amount_line(ln):
            continue
        if _line_has_unit_trap(ln):
            continue
        if "total" in low and "subtotal" not in low:
            vals = _line_amounts(ln)
            if vals:
                return (float(max(vals)), 0.88)

    # 3) Fallback: best max amount in bottom (still excluding tender/units)
    vals = []
    for ln in bottom:
        if _is_excluded_amount_line(ln):
            continue
        if _line_has_unit_trap(ln):
            continue
        vals.extend(_line_amounts(ln))
    if vals:
        return (float(max(vals)), 0.68)

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




