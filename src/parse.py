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

# Money tokens:
# - with cents: 30.74, 30,74, 1,234.56
# - sometimes OCR drops punctuation: 3074 (we do NOT want to trust that as money)
MONEY_WITH_CENTS_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,7}(?:,[0-9]{3})*(?:[.,][0-9]{2}))\b")
MONEY_ANY_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,7}(?:,[0-9]{3})*(?:[.,][0-9]{2})?)\b")

TOTAL_KEYS_STRONG = [
    "grand total", "amount due", "total due", "balance due",
    "total amount", "order total", "invoice total",
    "total", "amount paid", "sale total", "total sale", "sale:",
]

EXCLUDE_AMOUNT_KEYS = [
    "discount", "coupon", "change", "cash", "tender", "tip", "gratuity", "service charge",
    "auth", "approval", "authorized", "ref", "trans", "transaction",
    "debit", "credit", "visa", "mastercard", "mc", "amex", "discover",
    "card#", "card #", "expdate", "entry method", "aid", "tsi",
]

# Vendor should NEVER come from these sections
VENDOR_HARD_EXCLUDE = [
    # payment/tender section
    "visa", "discover", "mastercard", "amex", "debit", "credit",
    "card#", "card #", "expdate", "auth", "authorization",
    "entry method", "chip", "tap", "swiped", "aid", "tsi",
    "customer copy", "merchant copy", "refund", "return policy", "policy", "visit ",
    # register/receipt internals
    "cashier", "cshr", "reg:", "reg ", "trx", "str:", "store#", "store #",
    "rewards", "member", "isbn", "upc", "sku",
]

# lines that are commonly item/line-items (not vendor)
ITEM_LINE_HINTS = re.compile(
    r"\b(qty|ea\b|each|@|isbn|upc|sku|\b97\d{11}\b|\b978\d{10}\b)\b",
    re.I,
)

VENDOR_NOISE = {
    "thank you", "thanks", "welcome", "come again",
    "receipt", "invoice", "copy",
    "subtotal", "tax", "change", "cash",
    "approved", "authorization", "auth", "ref", "transaction", "trans",
    "visit", "returns", "return policy", "refund", "policy", "www", "http",
}

ADDRESS_HINTS = {
    "st", "street", "rd", "road", "ave", "avenue", "blvd", "boulevard", "ln", "lane",
    "dr", "drive", "hwy", "highway", "suite", "ste", "unit",
    "pa", "nj", "ny", "ca", "tx", "fl", "il", "oh", "wa", "va", "md", "ma", "ct",
}

MERCHANT_BOOST = [
    "inc", "llc", "corp", "company", "co", "store", "market",
    "restaurant", "cafe", "coffee", "diner", "grill", "bar",
    "gas", "station", "pharmacy", "hardware", "auto",
    "books", "book", "bookseller", "bookstore",
    "barnes", "noble",
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

def _has_date_or_time(line: str) -> bool:
    t = line or ""
    return bool(
        DATE_YMD_RE.search(t) or DATE_MDY_RE.search(t) or DATE_DOT_RE.search(t)
        or MONTH_NAME_RE.search(t) or TIME_RE.search(t)
    )

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

def _parse_money_val(token: str) -> Optional[float]:
    tok = (token or "").strip()
    if not tok:
        return None

    # normalize comma-decimal
    if "," in tok and "." not in tok and re.search(r"\d+,\d{2}$", tok):
        tok = tok.replace(",", ".")
    else:
        tok = tok.replace(",", "")

    try:
        val = float(tok)
    except Exception:
        return None

    if val <= 0 or val > 99999:
        return None

    return float(val)

def _line_cents_amounts(line: str) -> List[float]:
    vals: List[float] = []
    for tok in MONEY_WITH_CENTS_RE.findall(line or ""):
        v = _parse_money_val(tok)
        if v is not None:
            vals.append(v)
    return vals

def _line_any_amounts(line: str) -> List[float]:
    vals: List[float] = []
    for tok in MONEY_ANY_RE.findall(line or ""):
        v = _parse_money_val(tok)
        if v is not None:
            vals.append(v)
    return vals

def _is_excluded_amount_line(line: str) -> bool:
    low = _norm(line)
    return any(k in low for k in EXCLUDE_AMOUNT_KEYS)

def _looks_like_vendor_noise(line: str) -> bool:
    low = _norm(line)
    if not low:
        return True
    if any(k in low for k in VENDOR_NOISE):
        return True
    if any(k in low for k in VENDOR_HARD_EXCLUDE):
        return True
    if PHONE_RE.search(line):
        return True
    if _has_date_or_time(line):
        return True
    if _looks_like_address(line):
        return True
    if len(line) > 110:
        return True
    if ITEM_LINE_HINTS.search(line):
        return True
    return False

def _is_garbage_vendor(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    if len(s) <= 4 and " " not in s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    if letters < 5:
        return True
    # if it contains lots of commas/periods and no merchant keywords, often item text
    if (s.count(",") + s.count(".")) >= 2 and not any(k in _norm(s) for k in ["barnes", "noble", "store", "market", "inc", "llc", "company"]):
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

    # Vendor usually near top, but allow deeper because OCR junk sometimes precedes
    score += max(0.0, 1.0 - 0.03 * idx)

    letters = sum(1 for c in line if c.isalpha())
    digits = sum(1 for c in line if c.isdigit())
    score += 0.045 * letters
    score -= 0.035 * digits

    # MASSIVE boost for known merchant tokens
    if any(k in low for k in ["barnes", "noble"]):
        score += 3.5
    if any(k in low for k in MERCHANT_BOOST):
        score += 1.2

    # penalize item/title-like punctuation + long descriptive lines
    if ":" in line:
        score -= 0.6
    if len(line) > 42 and not any(k in low for k in ["barnes", "noble", "store", "market", "inc", "llc", "company"]):
        score -= 1.2

    # prefer 1–10 words
    w = line.split()
    if 1 <= len(w) <= 10:
        score += 0.35
    else:
        score -= 0.7

    return score

def extract_vendor(raw_text: str) -> Tuple[str, float, List[str]]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    window = lines[:90]

    candidates: List[Tuple[str, int]] = []
    for i, a in enumerate(window):
        if a:
            candidates.append((a, i))
        if i + 1 < len(window):
            b = window[i + 1]
            if 3 <= len(a) <= 70 and 3 <= len(b) <= 70:
                if not _looks_like_vendor_noise(a) and not _looks_like_vendor_noise(b):
                    candidates.append((f"{a} {b}", i))

    scored = sorted(((c, _vendor_score(c, idx)) for c, idx in candidates), key=lambda x: x[1], reverse=True)
    if not scored or scored[0][1] < -100:
        return ("", 0.0, [])

    best = _collapse_spaced_letters(scored[0][0]).strip()

    seen = set()
    cand_list = []
    for c, s in scored[:18]:
        c2 = _collapse_spaced_letters(c).strip()
        k = _norm(c2)
        if k and k not in seen and s > -50:
            seen.add(k)
            cand_list.append(c2)
        if len(cand_list) >= 3:
            break

    top_score = scored[0][1]
    if top_score >= 4.5:
        conf = 0.93
    elif top_score >= 3.2:
        conf = 0.86
    elif top_score >= 2.3:
        conf = 0.76
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
# Amount (subtotal + tax fallback, cents-only for tax/subtotal lines)
# -----------------------------
def extract_amount(raw_text: str) -> Tuple[float, float]:
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return (0.0, 0.0)

    n = len(lines)
    bottom_start = int(n * 0.35) if n >= 10 else 0
    bottom = lines[bottom_start:]

    subtotal: Optional[float] = None
    tax: Optional[float] = None

    # 1) capture subtotal and tax using cents-only values (avoids grabbing "11" or "6.000%")
    for ln in bottom:
        low = _norm(ln)

        if "subtotal" in low and subtotal is None:
            cents_vals = _line_cents_amounts(ln)
            if cents_vals:
                subtotal = float(cents_vals[-1])  # take last cents value on line

        if ("sales tax" in low or (("tax" in low) and "subtotal" not in low)) and tax is None:
            cents_vals = _line_cents_amounts(ln)
            if cents_vals:
                tax = float(cents_vals[-1])

    computed_total = None
    if subtotal is not None and tax is not None:
        computed_total = round(subtotal + tax, 2)

    # 2) try explicit TOTAL lines (prefer cents amounts)
    for ln in reversed(bottom):
        low = _norm(ln)
        if _is_excluded_amount_line(ln):
            continue
        if "total" in low or any(k in low for k in TOTAL_KEYS_STRONG):
            cents_vals = _line_cents_amounts(ln)
            if cents_vals:
                cand = float(max(cents_vals))

                if computed_total is not None and abs(cand - computed_total) <= 0.05:
                    return (computed_total, 0.97)

                # reject insane totals vs computed_total (OCR "30574" etc)
                if computed_total is not None and cand > computed_total * 10:
                    continue

                if cand <= 10000:
                    return (cand, 0.90)

    # 3) if total line was bad/missing, trust computed subtotal+tax
    if computed_total is not None:
        return (computed_total, 0.93)

    # 4) fallback: biggest cents amount in bottom (reasonable)
    cents_pool: List[float] = []
    for ln in bottom:
        if _is_excluded_amount_line(ln):
            continue
        cents_pool.extend(_line_cents_amounts(ln))
    cents_pool = [v for v in cents_pool if 0 < v <= 10000]
    if cents_pool:
        return (float(max(cents_pool)), 0.70)

    # 5) last resort
    any_pool: List[float] = []
    for ln in bottom:
        if _is_excluded_amount_line(ln):
            continue
        any_pool.extend(_line_any_amounts(ln))
    any_pool = [v for v in any_pool if 0 < v <= 10000]
    if any_pool:
        return (float(max(any_pool)), 0.60)

    return (0.0, 0.0)


# -----------------------------
# Public API
# -----------------------------
def extract_fields(raw_text: str) -> Dict:
    vendor, vconf, vcands = extract_vendor(raw_text)
    date, dconf = extract_date(raw_text)
    amount, acon = extract_amount(raw_text)

    parse_conf = 0.45 * float(vconf) + 0.35 * float(acon) + 0.20 * float(dconf)

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





