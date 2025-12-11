from __future__ import annotations

import io
import os
import zipfile
from typing import Dict, List

import pandas as pd

def make_csv_bytes(rows: List[Dict]) -> bytes:
    df = pd.DataFrame(rows)
    # friendly column ordering
    cols = [
        "receipt_date", "vendor", "amount", "category", "confidence",
        "uploaded_at", "original_filename", "stored_filename", "file_path", "id"
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    return df.to_csv(index=False).encode("utf-8")

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

