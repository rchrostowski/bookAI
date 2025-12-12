from __future__ import annotations

import csv
import io
import uuid
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TRANSACTIONS_CSV = "transactions.csv"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _csv_path(ws_dir: Path) -> Path:
    return ws_dir / TRANSACTIONS_CSV


def _fieldnames() -> List[str]:
    return [
        "id",
        "group_id",
        "date",
        "vendor",
        "amount",
        "category",
        "account_code",
        "job",
        "notes",
        "confidence",
        "confidence_notes",
        "needs_review",
        "receipt_path",
        "receipt_hash",
        "created_at",
        "updated_at",
        "approved_at",
        "deleted",
        "deleted_at",
    ]


def ensure_store(ws_dir: Path) -> None:
    p = _csv_path(ws_dir)
    if p.exists():
        return
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_fieldnames())
        w.writeheader()


def _read_all(ws_dir: Path) -> List[Dict]:
    ensure_store(ws_dir)
    p = _csv_path(ws_dir)
    rows: List[Dict] = []
    with p.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            row["amount"] = float(row.get("amount") or 0)
            row["confidence"] = float(row.get("confidence") or 0)
            row["needs_review"] = int(row.get("needs_review") or 0)
            row["deleted"] = int(row.get("deleted") or 0)
            rows.append(row)
    rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return rows


def _write_all(ws_dir: Path, rows: List[Dict]) -> None:
    p = _csv_path(ws_dir)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_fieldnames())
        w.writeheader()
        for r in rows:
            rr = dict(r)
            rr["amount"] = f"{float(rr.get('amount') or 0):.2f}"
            rr["confidence"] = f"{float(rr.get('confidence') or 0):.2f}"
            rr["needs_review"] = str(int(rr.get("needs_review") or 0))
            rr["deleted"] = str(int(rr.get("deleted") or 0))
            w.writerow({k: rr.get(k, "") for k in _fieldnames()})


def list_txns(ws_dir: Path, include_deleted: bool = False, only_deleted: bool = False) -> List[Dict]:
    rows = _read_all(ws_dir)
    if only_deleted:
        return [r for r in rows if int(r.get("deleted") or 0) == 1]
    if include_deleted:
        return rows
    return [r for r in rows if int(r.get("deleted") or 0) == 0]


def _hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:24]


def add_txn(
    ws_dir: Path,
    *,
    date: str,
    vendor: str,
    amount: float,
    category: str,
    account_code: str,
    confidence: float,
    confidence_notes: str = "",
    job: str = "",
    notes: str = "",
    receipt_bytes: bytes,
    receipt_filename: str,
    group_id: str = "",
) -> str:
    ensure_store(ws_dir)

    txn_id = uuid.uuid4().hex[:12]
    created_at = _now()
    receipt_hash = _hash_bytes(receipt_bytes)

    safe_name = receipt_filename.replace("/", "_").replace("\\", "_")
    receipt_rel = f"receipts/{txn_id}_{safe_name}"
    receipt_abs = ws_dir / receipt_rel
    receipt_abs.parent.mkdir(parents=True, exist_ok=True)
    receipt_abs.write_bytes(receipt_bytes)

    needs_review = int(confidence < 0.75 or not vendor or not date or float(amount) <= 0)

    row = {
        "id": txn_id,
        "group_id": group_id or "",
        "date": (date or "").strip(),
        "vendor": (vendor or "").strip(),
        "amount": float(amount),
        "category": (category or "Other").strip(),
        "account_code": (account_code or "").strip(),
        "job": (job or "").strip(),
        "notes": (notes or "").strip(),
        "confidence": float(confidence),
        "confidence_notes": (confidence_notes or "").strip(),
        "needs_review": needs_review,
        "receipt_path": receipt_rel,
        "receipt_hash": receipt_hash,
        "created_at": created_at,
        "updated_at": "",
        "approved_at": "",
        "deleted": 0,
        "deleted_at": "",
    }

    rows = _read_all(ws_dir)
    rows.append(row)
    _write_all(ws_dir, rows)
    return txn_id


def update_txn(ws_dir: Path, txn_id: str, patch: Dict) -> None:
    rows = _read_all(ws_dir)
    for r in rows:
        if r["id"] == txn_id:
            r.update(patch)
            r["amount"] = float(r.get("amount") or 0)
            r["confidence"] = float(r.get("confidence") or 0)

            conf = float(r.get("confidence") or 0)
            amt = float(r.get("amount") or 0)
            needs = int(conf < 0.75 or not r.get("vendor") or not r.get("date") or amt <= 0)
            r["needs_review"] = needs

            r["updated_at"] = _now()
            break
    _write_all(ws_dir, rows)


# ✅ THESE ARE THE FUNCTIONS YOUR APP IMPORTS
def soft_delete_txn(ws_dir: Path, txn_id: str) -> None:
    rows = _read_all(ws_dir)
    for r in rows:
        if r["id"] == txn_id and int(r.get("deleted") or 0) == 0:
            r["deleted"] = 1
            r["deleted_at"] = _now()
            r["updated_at"] = _now()
            break
    _write_all(ws_dir, rows)


def undo_delete_txn(ws_dir: Path, txn_id: str) -> None:
    rows = _read_all(ws_dir)
    for r in rows:
        if r["id"] == txn_id and int(r.get("deleted") or 0) == 1:
            r["deleted"] = 0
            r["deleted_at"] = ""
            r["updated_at"] = _now()
            break
    _write_all(ws_dir, rows)


def purge_deleted_txn(ws_dir: Path, txn_id: str) -> None:
    rows = _read_all(ws_dir)
    kept = []
    to_delete_path: Optional[str] = None

    for r in rows:
        if r["id"] == txn_id and int(r.get("deleted") or 0) == 1:
            to_delete_path = r.get("receipt_path") or None
        else:
            kept.append(r)

    if to_delete_path:
        fpath = ws_dir / to_delete_path
        if fpath.exists():
            try:
                fpath.unlink()
            except Exception:
                pass

    _write_all(ws_dir, kept)


def build_accountant_pack(ws_dir: Path) -> Tuple[bytes, bytes]:
    import zipfile

    rows = list_txns(ws_dir, include_deleted=False)

    out = io.StringIO()
    out.write("Date,Vendor,Amount,Category,AccountCode,Job,Notes,ReceiptFilename,Confidence,ApprovedAt\n")
    for r in rows:
        receipt_fn = (r.get("receipt_path") or "").split("/")[-1]
        out.write(
            f"{r.get('date','')},{_csv_escape(r.get('vendor',''))},{float(r.get('amount') or 0):.2f},"
            f"{_csv_escape(r.get('category',''))},{_csv_escape(r.get('account_code',''))},"
            f"{_csv_escape(r.get('job',''))},{_csv_escape(r.get('notes',''))},"
            f"{_csv_escape(receipt_fn)},{float(r.get('confidence') or 0):.2f},{_csv_escape(r.get('approved_at',''))}\n"
        )
    csv_bytes = out.getvalue().encode("utf-8")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            rel = r.get("receipt_path") or ""
            src = ws_dir / rel
            if not rel or not src.exists():
                continue
            month = (r.get("date") or "unknown")[:7]
            cat = (r.get("category") or "Other").replace("/", "-")
            dest = f"{month}/{cat}/{src.name}"
            z.write(src, dest)
    zip_bytes = zip_buf.getvalue()

    return csv_bytes, zip_bytes


def build_monthly_pnl_csv(pnl_df) -> bytes:
    return pnl_df.to_csv().encode("utf-8")


def _csv_escape(x: str) -> str:
    x = str(x or "")
    if any(c in x for c in [",", '"', "\n"]):
        x = '"' + x.replace('"', '""') + '"'
    return x

