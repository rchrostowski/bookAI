from __future__ import annotations

import os
import re
from datetime import datetime
from uuid import uuid4

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def new_id() -> str:
    return str(uuid4())

def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")

def safe_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^a-zA-Z0-9_\-\.]", "", name)
    return name[:120] if name else f"file_{new_id()}"

