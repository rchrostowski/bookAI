from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

TRANSACTIONS_CSV = "transactions.csv"


@dataclass
class Txn:
    id: str
    date: str
    vendor: str
    amount: float
    category: str
    account_code: str
    confidence: float
    job: str
    notes: str
    receipt_path: str
    created_at: str
    needs_review: int


def _csv_path(ws_dir: Path) -> Path:
    return ws_dir / TRANSACTIONS_CSV


def ensure_store(ws_dir: Path) -> None:
    p = _csv_path(ws_dir)
    if p.exists():
        return
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "id","date","vendor","amount","category","account_code","confidence",
                "job","notes","receipt_path","created_at","needs_review"
            ],
        )
        w.writeheader()


def list_txns(ws_dir: Path) -> List[Dict]:
    ensure_store(ws_dir)
    p = _csv_path(ws_dir)
    rows: List[Dict] = []
    with p.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            # normalize types
            row["amount"] = float(row.get("amount") or 0)
            row["confidence"] = float(row.get("confidence") or 0)
            row["needs_review"] = int(row.get("needs_review") or 0)
            rows.append(row)
    # newest first
    rows.sort(key=lambda x: x.get("created_at",""), reverse=True)
    return rows


def add_txn(
    ws_dir: Path,
    *,
    date: str,
    vendor: str,
    amount: float,
    category: str,
    account_code: str,
    confidence: float,
    job: str,
    notes: str,
    receipt_bytes: bytes,
    receipt_filename: str,
) -> str:
    ensure_store(ws_dir)

    txn_id = uuid.uuid4().hex[:12]
    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # store receipt
    safe_name = receipt_filename.replace("/", "_").replace("\\", "_")
    receipt_rel = f"receipts/{txn_id}_{safe_name}"
    receipt_abs = ws_dir / receipt_rel
    receipt_abs.write_bytes(receipt_bytes)

    needs_review = int(confidence < 0.75 or not vendor or not date or amount <= 0)

    row = {
        "id": txn_id,
        "date": date or "",
        "vendor": vendor or "",
        "amount": f"{float(amount):.2f}",
        "category": category or "Other",
        "account_code": account_code or "",
        "confidence": f"{float(confidence):.2f}",
        "job": (job or "").strip(),
        "notes": (notes or "").strip(),
        "receipt_path": receipt_rel,
        "created_at": created_at,
        "needs_review": str(needs_review),
    }

    p = _csv_path(ws_dir)
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        w.writerow(row)

    return txn_id


def update_txn(ws_dir: Path, txn_id: str, patch: Dict) -> None:
    ensure_store(ws_dir)
    rows = list_txns(ws_dir)
    for r in rows:
        if r["id"] == txn_id:
            r.update(patch)
            # recompute needs_review if relevant
            conf = float(r.get("confidence") or 0)
            amt = float(r.get("amount") or 0)
            needs = int(conf < 0.75 or not r.get("vendor") or not r.get("date") or amt <= 0)
            r["needs_review"] = needs
            break

    p = _csv_path(ws_dir)
    with p.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "id","date","vendor","amount","category","account_code","confidence",
            "job","notes","receipt_path","created_at","needs_review"
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            # keep numeric formatting stable
            r2 = dict(r)
            r2["amount"] = f"{float(r2.get('amount') or 0):.2f}"
            r2["confidence"] = f"{float(r2.get('confidence') or 0):.2f}"
            w.writerow(r2)


def delete_txn(ws_dir: Path, txn_id: str) -> None:
    ensure_store(ws_dir)
    rows = list_txns(ws_dir)

    kept = []
    receipt_to_delete: Optional[str] = None
    for r in rows:
        if r["id"] == txn_id:
            receipt_to_delete = r.get("receipt_path")
        else:
            kept.append(r)

    if receipt_to_delete:
        fpath = ws_dir / receipt_to_delete
        if fpath.exists():
            fpath.unlink()

    p = _csv_path(ws_dir)
    with p.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "id","date","vendor","amount","category","account_code","confidence",
            "job","notes","receipt_path","created_at","needs_review"
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in kept:
            r2 = dict(r)
            r2["amount"] = f"{float(r2.get('amount') or 0):.2f}"
            r2["confidence"] = f"{float(r2.get('confidence') or 0):.2f}"
            w.writerow(r2)


def build_accountant_pack(ws_dir: Path) -> tuple[bytes, bytes]:
    """
    Returns (csv_bytes, zip_bytes)
    ZIP organized: YYYY-MM/<Category>/receiptfile
    """
    import zipfile

    rows = list_txns(ws_dir)

    # CSV
    out = io.StringIO()
    out.write("Date,Vendor,Amount,Category,AccountCode,Job,Notes,ReceiptFilename,Confidence\n")
    for r in rows:
        receipt_fn = (r.get("receipt_path") or "").split("/")[-1]
        out.write(
            f"{r.get('date','')},{_csv_escape(r.get('vendor',''))},{float(r.get('amount') or 0):.2f},"
            f"{_csv_escape(r.get('category',''))},{_csv_escape(r.get('account_code',''))},"
            f"{_csv_escape(r.get('job',''))},{_csv_escape(r.get('notes',''))},"
            f"{_csv_escape(receipt_fn)},{float(r.get('confidence') or 0):.2f}\n"
        )
    csv_bytes = out.getvalue().encode("utf-8")

    # ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            rel = r.get("receipt_path") or ""
            src = ws_dir / rel
            if not rel or not src.exists():
                continue
            month = (r.get("date") or "unknown")[:7]  # YYYY-MM
            cat = (r.get("category") or "Other").replace("/", "-")
            dest = f"{month}/{cat}/{src.name}"
            z.write(src, dest)
    zip_bytes = zip_buf.getvalue()

    return csv_bytes, zip_bytes


def _csv_escape(x: str) -> str:
    x = str(x or "")
    if any(c in x for c in [",", '"', "\n"]):
        x = '"' + x.replace('"', '""') + '"'
    return x
