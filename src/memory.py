from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

MEMORY_FILE = "memory.json"

def _path(ws_dir: Path) -> Path:
    return ws_dir / MEMORY_FILE

def load_memory(ws_dir: Path) -> Dict:
    p = _path(ws_dir)
    if not p.exists():
        return {"vendor_map": {}, "jobs": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"vendor_map": {}, "jobs": []}

def save_memory(ws_dir: Path, mem: Dict) -> None:
    _path(ws_dir).write_text(json.dumps(mem, indent=2), encoding="utf-8")

def _norm_vendor(v: str) -> str:
    v = (v or "").strip().lower()
    v = "".join(ch for ch in v if ch.isalnum() or ch in [" ", "-"])
    return " ".join(v.split())

def remember_vendor_mapping(mem: Dict, vendor: str, category: str, account_code: str) -> None:
    v = _norm_vendor(vendor)
    if not v:
        return
    mem.setdefault("vendor_map", {})
    mem["vendor_map"][v] = {"category": category or "Other", "account_code": account_code or ""}

def get_vendor_mapping(mem: Dict, vendor: str):
    v = _norm_vendor(vendor)
    return (mem or {}).get("vendor_map", {}).get(v)

def remember_job(mem: Dict, job: str) -> None:
    job = (job or "").strip()
    if not job:
        return
    mem.setdefault("jobs", [])
    if job not in mem["jobs"]:
        mem["jobs"].append(job)
        mem["jobs"] = sorted(list(set(mem["jobs"])))

def get_known_jobs(mem: Dict) -> List[str]:
    return sorted([j for j in (mem or {}).get("jobs", []) if str(j).strip()])

