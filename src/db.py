from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA = """
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

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()

def insert_receipt(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    cols = ",".join(row.keys())
    qs = ",".join(["?"] * len(row))
    conn.execute(f"INSERT INTO receipts ({cols}) VALUES ({qs})", list(row.values()))
    conn.commit()

def list_receipts(
    conn: sqlite3.Connection,
    year: Optional[int] = None,
    category: Optional[str] = None,
    vendor: Optional[str] = None,
    search: Optional[str] = None,
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
    rows = conn.execute(f"SELECT DISTINCT {field} AS v FROM receipts WHERE {field} IS NOT NULL AND {field} != '' ORDER BY v").fetchall()
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

