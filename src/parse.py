from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional, Tuple

TOTAL_LABELS = ["TOTAL", "AMOUNT DUE", "BALANCE DUE", "TOTAL DUE", "GRAND TOTAL", "TOTAL SALE", "AMT DUE"]
AVOID_TOTAL_WORDS = ["SUBTOTAL", "SUB TOTAL", "TAX", "SALES TAX", "TIP", "GRATUITY", "CHANGE", "CASH", "VISA", "MASTERCARD", "AMEX", "DISCOUNT", "DISC"]

VENDOR_JUNK = [
    "THANK YOU", "THANKS", "WELCOME", "CUSTOMER COPY", "MERCHANT COPY",
    "APPROVED", "DECLINED", "PLEASE COME AGAIN", "COPY", "RECEIPT", "INVOICE"
]

def extract_fields(raw_text: str) -> Dict:
    text = raw_text or ""
    lines = _clean_lines(text)

    vendor = _extract_vendor(lines)
    date = _extract_date(text, lines)
    amount = _extract_total_amount(text, lines)

    return {"vendor": vendor, "date": date, "amount": amount}


def _clean_lines(text: str) -> list[str]:
    raw_lines = [l.strip() for l in (text or "").splitlines()]
    raw_lines = [re.sub(r"\s+", " ", l) for l in raw_lines]
    raw_lines = [l for l in raw_lines if l]
    return [l for l in raw_lines if len(l) > 1]


def _extract_vendor(lines: list[str]) -> str:
    if not lines:
        return ""

    top = lines[:15]
    candidates = []

    for l in top:
        u = l.upper()

        if any(j in u for j in VENDOR_JUNK):
            continue
        if _looks_like_phone(u) or _looks_like_date_line(u):
            continue

        digit_ratio = sum(ch.isdigit() for ch in u) / max(1, len(u))
        if digit_ratio > 0.55:
            continue

        score = 0
        if u == l:
            score += 2
        if 4 <= len(l) <= 45:
            score += 2
        if re.search(r"[A-Z]", u):
            score += 1
        if re.search(r"(COFFEE|CAFE|BAR|GRILL|RESTAURANT|SUPPLY|HARDWARE|MARKET|STORE|INC|LLC)", u):
            score += 2

        candidates.append((score, l))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1].strip()

    # Fallback: first reasonable line
    for l in top:
        u = l.upper()
        if any(j in u for j in VENDOR_JUNK):
            continue
        if _looks_like_phone(u) or _looks_like_date_line(u):
            continue
        return l.strip()

    return top[0].strip()


def _extract_date(text: str, lines: list[str]) -> str:
    top_text = "\n".join(lines[:25])
    candidates = []
    for src, weight in [(top_text, 2.0), (text, 1.0)]:
        for dt, _span in _find_dates(src):
            candidates.append((weight, dt))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][1].strftime("%Y-%m-%d")


def _find_dates(s: str) -> list[Tuple[datetime, Tuple[int, int]]]:
    out: list[Tuple[datetime, Tuple[int, int]]] = []
    if not s:
        return out

    for m in re.finditer(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b", s):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        dt = _safe_date(y, mo, d)
        if dt:
            out.append((dt, m.span()))

    for m in re.finditer(r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-](\d{2}|\d{4})\b", s):
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y = 2000 + y
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
    labeled = []

    for i, line in enumerate(lines):
        u = line.upper()
        if not any(lbl in u for lbl in TOTAL_LABELS):
            continue
        if any(bad in u for bad in AVOID_TOTAL_WORDS):
            continue

        near = [line]
        if i + 1 < len(lines):
            near.append(lines[i + 1])
        if i - 1 >= 0:
            near.append(lines[i - 1])

        amounts = []
        for chunk in near:
            amounts.extend(_find_amounts(chunk))

        amounts = [a for a in amounts if 0.50 <= a <= 20000.0]
        if amounts:
            labeled.append(max(amounts))

    if labeled:
        return float(max(labeled))

    all_amounts = [a for a in _find_amounts(text) if 0.50 <= a <= 20000.0]
    return float(max(all_amounts)) if all_amounts else 0.0


def _find_amounts(s: str) -> list[float]:
    if not s:
        return []
    out = []
    for m in re.finditer(r"(?<!\d)(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2}))", s):
        raw = m.group(1).replace("$", "").replace(",", "").strip()
        try:
            out.append(float(raw))
        except Exception:
            pass
    return out


def _looks_like_phone(s: str) -> bool:
    return bool(re.search(r"\b(\+?1[-\s]?)?\(?\d{3}\)?[-\s]?\d{3}[-\s]?\d{4}\b", s))


def _looks_like_date_line(s: str) -> bool:
    return bool(re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b", s)) or bool(
        re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-](\d{2}|\d{4}))\b", s)
    )


