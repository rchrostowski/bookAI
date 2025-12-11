from __future__ import annotations

import io
import os
import zipfile
from typing import Dict, List, Optional

import pandas as pd

def make_accountant_summary_csv(rows: List[Dict]) -> bytes:
    df = pd.DataFrame(rows)
    cols = [
        "receipt_date", "vendor", "amount", "txn_type",
        "category", "account_code", "confidence", "reviewed",
        "uploaded_at", "original_filename", "stored_filename", "file_path", "id"
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    return df.to_csv(index=False).encode("utf-8")

def make_quickbooks_csv(rows: List[Dict], company_name: Optional[str] = None) -> bytes:
    """
    "QuickBooks-friendly" generic CSV.
    (QB has multiple import flows; this format is readable and typically mappable.)
    """
    df = pd.DataFrame(rows)

    def _memo(r):
        bits = []
        if company_name:
            bits.append(company_name)
        if r.get("category"):
            bits.append(str(r.get("category")))
        if r.get("id"):
            bits.append(f"id:{r.get('id')}")
        return " | ".join(bits)

    out = pd.DataFrame({
        "Date": df.get("receipt_date"),
        "Type": df.get("txn_type", "Expense"),
        "Vendor": df.get("vendor"),
        "Description": df.get("original_filename"),
        "Account": df.get("account_code").fillna("").astype(str),
        "Category": df.get("category"),
        "Amount": df.get("amount"),
        "Memo": df.apply(_memo, axis=1),
        "ReceiptFilename": df.get("stored_filename"),
    })

    # For QB mapping, it's often helpful to have debits as positive for Expenses
    # and revenue as positive too; user can flip sign if desired.
    return out.to_csv(index=False).encode("utf-8")

def make_monthly_pnl_csv(rows: List[Dict]) -> bytes:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["Month", "Revenue", "Expenses", "Net"]).to_csv(index=False).encode("utf-8")

    df["receipt_date"] = pd.to_datetime(df["receipt_date"], errors="coerce")
    df = df.dropna(subset=["receipt_date"])
    df["Month"] = df["receipt_date"].dt.to_period("M").astype(str)

    # Ensure amount numeric
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)

    rev = df[df.get("txn_type", "Expense") == "Revenue"].groupby("Month")["amount"].sum()
    exp = df[df.get("txn_type", "Expense") == "Expense"].groupby("Month")["amount"].sum()

    pnl = pd.DataFrame({
        "Revenue": rev,
        "Expenses": exp
    }).fillna(0.0)

    pnl["Net"] = pnl["Revenue"] - pnl["Expenses"]
    pnl = pnl.reset_index().rename(columns={"index": "Month"}).sort_values("Month")
    pnl = pnl[["Month", "Revenue", "Expenses", "Net"]]
    return pnl.to_csv(index=False).encode("utf-8")

def make_receipts_zip_bytes(rows: List[Dict]) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            p = r.get("file_path")
            if p and os.path.exists(p):
                arc = f"receipts/{r.get('stored_filename', os.path.basename(p))}"
                zf.write(p, arcname=arc)
    mem.seek(0)
    return mem.read()


