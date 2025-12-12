from __future__ import annotations

import re
from pathlib import Path

DATA_ROOT = Path("data")

def sanitize_workspace(code: str) -> str:
    code = (code or "").strip().lower()
    code = re.sub(r"[^a-z0-9\-]+", "-", code)
    code = re.sub(r"-{2,}", "-", code).strip("-")
    return code

def workspace_dir(code: str) -> Path:
    ws = sanitize_workspace(code)
    if not ws:
        raise ValueError("Workspace code is empty.")
    p = DATA_ROOT / ws
    p.mkdir(parents=True, exist_ok=True)
    (p / "receipts").mkdir(parents=True, exist_ok=True)
    return p
