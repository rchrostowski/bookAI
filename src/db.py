from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
  id TEXT PRIMARY KEY,
  uploaded_at TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  stored_filename TEXT NOT NULL,
  file_path TEXT NOT NULL,
  vendor TEXT,
  receipt_date TEXT,
  amount REAL,
  category TEXT,
  confidence REAL,
  raw_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_uploaded_at ON receipts(uploaded_at);
CREATE INDEX IF NOT EXISTS idx_category ON receipts(category);
CREATE INDEX IF NOT EXISTS idx_vendor ON receipts(vendor);
"""

# New columns we want (backwards-compatible via migration):
# txn_type: 'Expense' or 'Revenue'
# account_code: mapped chart-of-accounts code (string)
# reviewed: 0/1
MIGRATIONS = [
    ("txn_type", "TEXT", "'Expense'"),
    ("account_code", "TEXT", "''"),
    ("reviewed", "INTEGER", "0"),
]

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA)
    _migrate(conn)
    conn.commit()

def _migrate(conn: sqlite3.Connection) -> None:
    # Add missing columns if older DB already exists
    cols = conn.execute("PRAGMA table_info(receipts)").fetchall()
    existing = {c["name"] for c in cols}
    for name, coltype, default in MIGRATIONS:
        if name not in existing:
            conn.execute(f"ALTER TABLE receipts ADD COLUMN {name} {coltype} DEFAULT {default}")

def insert_receipt(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    cols = ",".join(row.keys())
    qs = ",".join(["?"] * len(row))
    conn.execute(f"INSERT INTO receipts ({cols}) VALUES ({qs})", list(row.values()))
    conn.commit()

def update_receipt(conn: sqlite3.Connection, receipt_id: str, updates: Dict[str, Any]) -> None:
    if not updates:
        return
    sets = ", ".join([f"{k} = ?" for k in updates.keys()])
    params = list(updates.values()) + [receipt_id]
    conn.execute(f"UPDATE receipts SET {sets} WHERE id = ?", params)
    conn.commit()

def list_receipts(
    conn: sqlite3.Connection,
    year: Optional[int] = None,
    category: Optional[str] = None,
    vendor: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,  # "All"|"Needs review"|"Reviewed"
    txn_type: Optional[str] = None # "All"|"Expense"|"Revenue"
) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []

    if year is not None:
        where.append("substr(receipt_date, 1, 4) = ?")
        params.append(str(year))

    if category and category != "All":
        where.append("category = ?")
        params.append(category)

    if vendor and vendor != "All":
        where.append("vendor = ?")
        params.append(vendor)

    if txn_type and txn_type != "All":
        where.append("txn_type = ?")
        params.append(txn_type)

    if status and status != "All":
        if status == "Needs review":
            where.append("reviewed = 0")
        elif status == "Reviewed":
            where.append("reviewed = 1")

    if search:
        where.append("(vendor LIKE ? OR raw_text LIKE ? OR original_filename LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""
        SELECT * FROM receipts
        {where_sql}
        ORDER BY uploaded_at DESC
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]

def get_distinct(conn: sqlite3.Connection, field: str) -> List[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {field} AS v FROM receipts WHERE {field} IS NOT NULL AND {field} != '' ORDER BY v"
    ).fetchall()
    return [r["v"] for r in rows]

def get_years(conn: sqlite3.Connection) -> List[int]:
    rows = conn.execute(
        "SELECT DISTINCT substr(receipt_date,1,4) AS y FROM receipts WHERE receipt_date IS NOT NULL AND receipt_date != '' ORDER BY y DESC"
    ).fetchall()
    out = []
    for r in rows:
        try:
            out.append(int(r["y"]))
        except Exception:
            pass
    return out

def delete_receipt(conn: sqlite3.Connection, receipt_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    conn.commit()
    return dict(row)

