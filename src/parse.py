from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Tuple, Optional


# -----------------------------
# Patterns
# -----------------------------
PHONE_RE = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(-\d{4})?\b")
MONEY_RE = re.compile(r"(?<!\w)\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{2})?)\b")
DATE_YMD_RE = re.compile(r"\b(20\d{2})[-/](\d{2})[-/](\d{2})\b")
DATE_MDY_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})(?::\d{2})?\b", re.I)

# Keywords for totals
TOTAL_KEYS_STRONG = ["grand total", "amount due", "total due", "balance due", "total"]
TOTAL_KEYS_WEAK = ["due", "balance"]
EXCLUDE_AMOUNT_KEYS = [
    "subtotal", "sub total", "tax", "sales tax", "vat", "discount", "change",
    "cash", "tender", "tip", "gratuity", "service charge",
    "auth", "approval", "authorized", "ref", "trans", "transaction",
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
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _has_money(line: str) -> bool:
    return bool(MONEY_RE.search(line))

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

def _looks_like_vendor_noise(line: str) -> bool:
    low = _norm(line)
    if not low:
        return True
    if any(k in low for k in VENDOR_NOISE):
        return True
    if PHONE_RE.search(line):
        return True
    if _has_money(line):
        return True
    if DATE_YMD_RE.search(line) or DATE_MDY_RE.search(line) or TIME_RE.search(line):
        return True
    if _looks_like_address(line):
        return True
    # very long lines are often policy text
    if len(line) > 44:
        return True
    return False

def _collapse_spaced_letters(s: str) -> str:
    # handles "S H E L L" → "SHELL"
    toks = s.split()
    if len(toks) >= 4 and all(len(t) == 1 for t in toks[:4]):
        return "".join(toks)
    return s

def _parse_money_val(token: str) -> Optional[float]:
    try:
        token = token.replace(",", "").strip()
        val = float(token)
        # ignore insanely large numbers (often IDs)
        if val > 99999:
            return None
        return val
    except Exception:
        return None


# -----------------------------
# Vendor extraction
# -----------------------------
def _vendor_score(line: str) -> float:
    line = _clean_line(line)
    if _looks_like_vendor_noise(line):
        return -999.0

    score = 0.0

    # Favor top-ish and "header-like" strings: uppercase ratio
    letters = sum(1 for c in line if c.isalpha())
    uppers = sum(1 for c in line if c.isalpha() and c.isupper())
    if letters > 0:
        score += 0.9 * (uppers / letters)

    # Penalize digits and punctuation
    digits = sum(1 for c in line if c.isdigit())
    punct = sum(1 for c in line if (not c.isalnum() and c != " "))
    score -= 0.18 * digits
    score -= 0.22 * punct

    # Prefer 1–5 words
    w = line.split()
    if 1 <= len(w) <= 5:
        score += 0.6
    else:
        score -= 0.3

    # Small boost if “merchant-y”
    low = _norm(line)
    if any(k in low for k in ["inc", "llc", "corp", "co", "company", "market", "store", "diner", "cafe", "coffee", "gas", "station"]):
        score += 0.25

    return score

def extract_vendor(raw_text: str) -> Tuple[str, float, List[str]]:
    """
    Returns: (vendor, vendor_confidence, candidates)
    """
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]

    # Vendor is almost always near the top
    top = lines[:22]

    # Candidate set includes merged adjacent short lines (split headers)
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

    # Build candidate list (top 3 unique)
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

    # Confidence from score (bounded)
    # Typical best score ~0.8–1.4
    conf = max(0.0, min(0.92, (scored[0][1] + 0.1)))
    conf = float(max(0.25, conf))  # never claim super low if we picked a candidate

    return (best, conf, cand_list)


# -----------------------------
# Date extraction
# -----------------------------
def extract_date(raw_text: str) -> Tuple[str, float]:
    text = raw_text or ""

    # Prefer ISO-like dates first
    m = DATE_YMD_RE.search(text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return (dt.strftime("%Y-%m-%d"), 0.95)
        except Exception:
            pass

    # MM/DD/YYYY or MM-DD-YYYY
    # Many receipts show date near top; take the first plausible
    for m in DATE_MDY_RE.finditer(text):
        mm, dd, yy = m.group(1), m.group(2), m.group(3)
        if len(yy) == 2:
            yy = "20" + yy
        try:
            dt = datetime(int(yy), int(mm), int(dd))
            # sanity window: 2010–2035
            if 2010 <= dt.year <= 2035:
                return (dt.strftime("%Y-%m-%d"), 0.85)
        except Exception:
            continue

    return ("", 0.0)


# -----------------------------
# Amount extraction
# -----------------------------
def _line_amounts(line: str) -> List[float]:
    vals = []
    for tok in MONEY_RE.findall(line or ""):
        v = _parse_money_val(tok)
        if v is not None:
            vals.append(v)
    return vals

def extract_amount(raw_text: str) -> Tuple[float, float]:
    """
    Strategy:
      1) Look for strongest TOTAL-like lines and pick the best amount there
      2) Otherwise, look for "TOTAL" lines (but exclude subtotal/tax/tip/change)
      3) Otherwise, fallback to max amount near bottom quarter of receipt
    """
    lines = [_clean_line(x) for x in (raw_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return (0.0, 0.0)

    # Weighted scan with priority for bottom area (totals usually lower)
    n = len(lines)
    bottom_start = int(n * 0.55)
    bottom = lines[bottom_start:] if n >= 8 else lines

    def is_excluded_total_line(l: str) -> bool:
        low = _norm(l)
        return any(k in low for k in EXCLUDE_AMOUNT_KEYS)

    # 1) Strong keys
    for ln in bottom:
        low = _norm(ln)
        if any(k in low for k in TOTAL_KEYS_STRONG) and not is_excluded_total_line(ln):
            vals = _line_amounts(ln)
            if vals:
                return (float(max(vals)), 0.95)

    # 2) Generic total lines, excluding traps
    for ln in bottom:
        low = _norm(ln)
        if ("total" in low) and ("subtotal" not in low) and (not is_excluded_total_line(ln)):
            vals = _line_amounts(ln)
            if vals:
                return (float(max(vals)), 0.88)

    # 3) Fallback: max amount in bottom, excluding obvious traps like change/tender
    fallback_vals = []
    for ln in bottom:
        if is_excluded_total_line(ln):
            continue
        fallback_vals.extend(_line_amounts(ln))
    if fallback_vals:
        return (float(max(fallback_vals)), 0.70)

    # 4) Last resort: max amount anywhere
    all_vals = []
    for ln in lines:
        if is_excluded_total_line(ln):
            continue
        all_vals.extend(_line_amounts(ln))
    if all_vals:
        return (float(max(all_vals)), 0.55)

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

    # Overall parse confidence: conservative
    # Vendor + amount matter most for "first-time feels right"
    parse_conf = 0.0
    parse_conf += 0.45 * vconf
    parse_conf += 0.35 * acon
    parse_conf += 0.20 * dconf

    return {
        "vendor": vendor,
        "date": date,
        "amount": float(amount),
        # extras (safe to ignore if your UI doesn't use them)
        "vendor_candidates": vcands,
        "vendor_confidence": float(vconf),
        "date_confidence": float(dconf),
        "amount_confidence": float(acon),
        "parse_confidence": float(round(parse_conf, 3)),
    }


