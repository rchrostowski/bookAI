from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd
import streamlit as st

from src.utils import ensure_dir, new_id, now_iso, safe_filename
from src.db import (
    connect,
    init_db,
    insert_receipt,
    list_receipts,
    get_distinct,
    get_years,
    delete_receipt,
    update_receipt,
)
from src.ocr import ocr_upload
from src.parse import extract_fields
from src.categorize import categorize, all_categories
from src.export import (
    make_accountant_summary_csv,
    make_quickbooks_csv,
    make_monthly_pnl_csv,
    make_receipts_zip_bytes,
)

APP_TITLE = "BookAI"
DATA_DIR = "data"
RECEIPTS_DIR = os.path.join(DATA_DIR, "receipts")
DB_PATH = os.path.join(DATA_DIR, "bookai.sqlite")
COA_PATH = os.path.join(DATA_DIR, "coa.json")

# ✅ Lower default so you don't get forced into review constantly
DEFAULT_REVIEW_THRESHOLD = 0.25

DEFAULT_COA: Dict[str, str] = {
    "Fuel": "6000",
    "Tools & Equipment": "6100",
    "Materials / Supplies": "6200",
    "Vehicle Maintenance": "6300",
    "Meals": "6400",
    "Office / Admin": "6500",
    "Subcontractors": "6600",
    "Permits / Fees": "6700",
    "Other": "6999",
}


@dataclass
class Extracted:
    vendor: str
    receipt_date: str
    amount: float
    category: str
    confidence: float


def setup():
    ensure_dir(DATA_DIR)
    ensure_dir(RECEIPTS_DIR)
    conn = connect(DB_PATH)
    init_db(conn)
    return conn


def load_coa() -> Dict[str, str]:
    if os.path.exists(COA_PATH):
        try:
            with open(COA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return dict(DEFAULT_COA)


def save_coa(coa: Dict[str, str]) -> None:
    with open(COA_PATH, "w", encoding="utf-8") as f:
        json.dump(coa, f, indent=2)


def infer_account_code(coa: Dict[str, str], category: str) -> str:
    return coa.get(category, coa.get("Other", ""))


def badge(text: str):
    st.markdown(
        f"""
        <span style="
            display:inline-block;
            padding:0.2rem 0.55rem;
            border-radius:999px;
            border:1px solid rgba(49,51,63,0.2);
            font-size:0.85rem;
            background:rgba(49,51,63,0.04);
        ">{text}</span>
        """,
        unsafe_allow_html=True,
    )


def should_need_review(ex: Extracted, threshold: float, ocr_text: str) -> bool:
    """
    Needs review is a *workflow suggestion*, not a blocker.
    We flag when:
      - OCR produced no text (common on cloud)
      - critical fields missing
      - confidence below threshold
    """
    if not (ocr_text or "").strip():
        return True
    if ex.confidence < threshold:
        return True
    if not ex.vendor.strip():
        return True
    if not ex.receipt_date.strip():
        return True
    if ex.amount is None or ex.amount <= 0:
        return True
    return False


def extract_from_upload(file_name: str, file_bytes: bytes) -> Tuple[Optional[Extracted], Optional[str], Optional[object], str]:
    try:
        preview_img, raw_text = ocr_upload(file_name, file_bytes)
    except Exception as e:
        return None, f"OCR failed: {e}", None, ""

    vendor, receipt_date, amount = extract_fields(raw_text)
    cat, conf = categorize(raw_text, vendor=vendor)

    ex = Extracted(
        vendor=vendor or "",
        receipt_date=receipt_date or "",
        amount=float(amount) if amount is not None else 0.0,
        category=cat,
        confidence=float(conf),
    )
    return ex, None, preview_img, raw_text


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    conn = setup()
    coa = load_coa()

    with st.sidebar:
        st.markdown(f"### {APP_TITLE}")
        st.caption("Receipt capture → organize → exports")
        page = st.radio(
            "Navigation",
            ["Upload", "Inbox (Needs review)", "Library", "Reports", "Exports", "Admin"],
            index=0,
        )

        st.divider()
        st.markdown("**Review sensitivity**")
        st.caption(
            "This threshold controls when items show up in **Needs review**. "
            "Lower = fewer interruptions."
        )
        review_threshold = st.slider(
            "Confidence threshold",
            0.10,
            0.95,
            float(st.session_state.get("review_threshold", DEFAULT_REVIEW_THRESHOLD)),
            0.01,
        )
        st.session_state["review_threshold"] = float(review_threshold)

        st.caption(
            "⚠️ Confidence is about **category certainty** (rule-based keywords), not whether the receipt is real."
        )

    # Header
    c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1])
    with c1:
        st.title(APP_TITLE)
        st.caption("A clean MVP for small-business receipt management.")
    with c2:
        badge("SQLite")
    with c3:
        badge("COA mapping")
    with c4:
        badge("Exports")

    # KPIs
    all_rows = list_receipts(conn, status="All")
    needs = [r for r in all_rows if int(r.get("reviewed", 0) or 0) == 0]
    reviewed = [r for r in all_rows if int(r.get("reviewed", 0) or 0) == 1]
    total_amount = sum(float(r.get("amount") or 0.0) for r in all_rows)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Receipts stored", f"{len(all_rows)}")
    k2.metric("Needs review", f"{len(needs)}")
    k3.metric("Reviewed", f"{len(reviewed)}")
    k4.metric("Total tracked ($)", f"{total_amount:,.2f}")

    st.divider()

    if page == "Upload":
        upload_page(conn, coa)
    elif page == "Inbox (Needs review)":
        inbox_page(conn, coa)
    elif page == "Library":
        library_page(conn)
    elif page == "Reports":
        reports_page(conn)
    elif page == "Exports":
        exports_page(conn)
    elif page == "Admin":
        admin_page(conn, coa)


def upload_page(conn, coa: Dict[str, str]):
    st.subheader("Upload")
    st.info(
        "Upload a receipt **photo (JPG/PNG)** or **PDF invoice**.\n\n"
        "- We try to read it automatically.\n"
        "- If confidence is low, it may appear in **Needs review**, but you can still save it.\n"
        "- You can always edit later in the Inbox."
    )

    uploaded = st.file_uploader("Upload receipt", type=["png", "jpg", "jpeg", "pdf"], accept_multiple_files=False)
    if not uploaded:
        st.caption("Tip: photos with good lighting and sharp text work best.")
        return

    file_bytes = uploaded.getvalue()
    file_name = uploaded.name

    with st.spinner("Processing receipt…"):
        ex, err, preview_img, raw_text = extract_from_upload(file_name, file_bytes)

    if err:
        st.error(err)
        return
    assert ex is not None

    threshold = float(st.session_state.get("review_threshold", DEFAULT_REVIEW_THRESHOLD))
    need_review_auto = should_need_review(ex, threshold, raw_text)

    left, right = st.columns([1.1, 1])

    with left:
        st.markdown("#### Preview")
        if preview_img is not None:
            st.image(preview_img, use_container_width=True)
        with st.expander("Raw OCR text (what the app sees)"):
            st.text(raw_text or "")

    with right:
        st.markdown("#### Details")
        st.caption(
            "If fields look wrong, just fix them here. Low confidence usually means the category keywords weren't strong."
        )

        with st.form("save_form", clear_on_submit=False):
            txn_type = st.selectbox("Type", ["Expense", "Revenue"], index=0)

            vendor = st.text_input("Vendor", value=ex.vendor)
            receipt_date = st.text_input("Date (YYYY-MM-DD)", value=ex.receipt_date)
            amount = st.number_input("Amount", value=float(ex.amount or 0.0), step=0.01)

            category_options = all_categories()[1:]  # skip "All"
            cat_index = category_options.index(ex.category) if ex.category in category_options else 0
            category = st.selectbox("Category", category_options, index=cat_index)

            account_code_default = infer_account_code(coa, category)
            account_code = st.text_input("Account code (Chart of Accounts)", value=account_code_default)

            st.metric("Category confidence", f"{ex.confidence:.0%}")

            # ✅ Default reviewed to True unless it's truly incomplete.
            reviewed = st.checkbox("Mark as reviewed", value=(not need_review_auto))

            if need_review_auto:
                st.warning(
                    "Flagged for review. This does NOT block saving — it just means you may want to double-check later."
                )

            save_btn = st.form_submit_button("Save receipt", type="primary")

        if save_btn:
            rid = new_id()
            safe_orig = safe_filename(file_name)
            stored_name = f"{rid}_{safe_orig}"
            save_path = os.path.join(RECEIPTS_DIR, stored_name)

            with open(save_path, "wb") as f:
                f.write(file_bytes)

            row = {
                "id": rid,
                "uploaded_at": now_iso(),
                "original_filename": file_name,
                "stored_filename": stored_name,
                "file_path": save_path,
                "vendor": vendor.strip(),
                "receipt_date": receipt_date.strip(),
                "amount": float(amount),
                "txn_type": txn_type,
                "category": category,
                "account_code": account_code.strip(),
                "confidence": float(ex.confidence),
                "reviewed": 1 if reviewed else 0,
                "raw_text": raw_text or "",
            }
            insert_receipt(conn, row)
            st.success("Saved. Next: check Library / Exports (Inbox only if you want to review).")


def inbox_page(conn, coa: Dict[str, str]):
    st.subheader("Inbox (Needs review)")
    st.info(
        "This is a **to-do list** of items you might want to double-check.\n\n"
        "Common reasons:\n"
        "- low category confidence\n"
        "- missing vendor/date/amount\n"
        "- OCR returned little/no text\n\n"
        "You can edit fields here and mark items as reviewed."
    )

    threshold = float(st.session_state.get("review_threshold", DEFAULT_REVIEW_THRESHOLD))
    rows = list_receipts(conn, status="Needs review")
    if not rows:
        st.success("Inbox is clean.")
        return

    # Filter only what truly needs review by current threshold OR incomplete
    def needs(r):
        conf = float(r.get("confidence") or 0.0)
        if int(r.get("reviewed") or 0) == 1:
            return False
        if conf < threshold:
            return True
        if not str(r.get("vendor") or "").strip():
            return True
        if not str(r.get("receipt_date") or "").strip():
            return True
        if float(r.get("amount") or 0.0) <= 0:
            return True
        if not str(r.get("raw_text") or "").strip():
            return True
        return False

    rows = [r for r in rows if needs(r)]
    if not rows:
        st.success("Nothing currently meets the review criteria.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df[["receipt_date", "vendor", "amount", "txn_type", "category", "account_code", "confidence", "original_filename", "id"]],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.markdown("#### Review one item")
    pick = st.selectbox("Select receipt ID", options=df["id"].tolist())
    r = next((x for x in rows if x["id"] == pick), None)
    if not r:
        return

    c1, c2 = st.columns([1.1, 1])
    with c1:
        path = r.get("file_path")
        if path and os.path.exists(path) and os.path.splitext(path)[1].lower() != ".pdf":
            st.image(path, use_container_width=True)
        with st.expander("Raw OCR text"):
            st.text(r.get("raw_text", ""))

    with c2:
        category_options = all_categories()[1:]
        with st.form("review_form"):
            vendor = st.text_input("Vendor", value=r.get("vendor", ""))
            receipt_date = st.text_input("Date (YYYY-MM-DD)", value=r.get("receipt_date", ""))
            amount = st.number_input("Amount", value=float(r.get("amount") or 0.0), step=0.01)
            txn_type = st.selectbox("Type", ["Expense", "Revenue"], index=0 if r.get("txn_type","Expense")=="Expense" else 1)

            cat = r.get("category") or "Other"
            cat_index = category_options.index(cat) if cat in category_options else category_options.index("Other")
            category = st.selectbox("Category", category_options, index=cat_index)

            account_code_default = r.get("account_code") or infer_account_code(coa, category)
            account_code = st.text_input("Account code", value=account_code_default)

            reviewed = st.checkbox("Mark as reviewed", value=True)
            submit = st.form_submit_button("Save changes", type="primary")

        if submit:
            update_receipt(conn, pick, {
                "vendor": vendor.strip(),
                "receipt_date": receipt_date.strip(),
                "amount": float(amount),
                "txn_type": txn_type,
                "category": category,
                "account_code": account_code.strip(),
                "reviewed": 1 if reviewed else 0
            })
            st.success("Updated.")


def library_page(conn):
    st.subheader("Library")
    st.info(
        "This is your permanent, searchable storage.\n\n"
        "Use filters to find receipts by year, vendor, category, type, and review status."
    )

    vendors = ["All"] + get_distinct(conn, "vendor")
    years = ["All"] + get_years(conn)
    cats = all_categories()
    statuses = ["All", "Needs review", "Reviewed"]
    txn_types = ["All", "Expense", "Revenue"]

    f1, f2, f3, f4, f5 = st.columns([1, 1, 1, 1, 2])
    with f1:
        year_sel = st.selectbox("Year", options=years, index=0)
    with f2:
        status_sel = st.selectbox("Status", options=statuses, index=0)
    with f3:
        txn_sel = st.selectbox("Type", options=txn_types, index=0)
    with f4:
        cat_sel = st.selectbox("Category", options=cats, index=0)
    with f5:
        search = st.text_input("Search", placeholder="vendor, filename, OCR text…")

    year_int = None if year_sel == "All" else int(year_sel)
    rows = list_receipts(
        conn,
        year=year_int,
        category=cat_sel,
        vendor=None,
        search=search.strip() if search else None,
        status=status_sel,
        txn_type=txn_sel,
    )

    vendor_sel = st.selectbox("Vendor", options=vendors, index=0)
    if vendor_sel != "All":
        rows = [r for r in rows if (r.get("vendor") or "") == vendor_sel]

    if not rows:
        st.info("No receipts match your filters.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df[["receipt_date", "vendor", "amount", "txn_type", "category", "account_code", "confidence", "reviewed", "uploaded_at", "original_filename", "id"]],
        use_container_width=True,
        hide_index=True,
    )


def reports_page(conn):
    st.subheader("Reports")
    st.info(
        "Monthly P&L is computed from items labeled **Revenue** vs **Expense**.\n\n"
        "Tip: if you only upload receipts (expenses), revenue will be $0 until you enter revenue transactions."
    )

    years = get_years(conn)
    if not years:
        st.info("Add receipts first.")
        return

    year = st.selectbox("Year", options=years, index=0)
    rows = list_receipts(conn, year=int(year), status="All")
    if not rows:
        st.info("No rows for that year.")
        return

    df = pd.DataFrame(rows)
    df["receipt_date"] = pd.to_datetime(df.get("receipt_date"), errors="coerce")
    df = df.dropna(subset=["receipt_date"])
    df["Month"] = df["receipt_date"].dt.to_period("M").astype(str)
    df["amount"] = pd.to_numeric(df.get("amount"), errors="coerce").fillna(0.0)
    df["txn_type"] = df.get("txn_type", "Expense").fillna("Expense")

    rev = df[df["txn_type"] == "Revenue"].groupby("Month")["amount"].sum()
    exp = df[df["txn_type"] == "Expense"].groupby("Month")["amount"].sum()

    pnl = pd.DataFrame({"Revenue": rev, "Expenses": exp}).fillna(0.0)
    pnl["Net"] = pnl["Revenue"] - pnl["Expenses"]
    pnl = pnl.reset_index().sort_values("Month")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Revenue", f"${pnl['Revenue'].sum():,.2f}")
    c2.metric("Total Expenses", f"${pnl['Expenses'].sum():,.2f}")
    c3.metric("Net", f"${pnl['Net'].sum():,.2f}")

    st.markdown("#### Monthly P&L")
    st.dataframe(pnl, use_container_width=True, hide_index=True)
    st.markdown("#### Trend")
    st.line_chart(pnl.set_index("Month")[["Revenue", "Expenses", "Net"]])


def exports_page(conn):
    st.subheader("Exports")
    st.info(
        "This generates your **year-end accountant pack**:\n"
        "- CSV summaries\n"
        "- QuickBooks-friendly CSV\n"
        "- Monthly P&L CSV\n"
        "- ZIP of original receipts\n"
    )

    years = get_years(conn)
    if not years:
        st.info("No receipts yet.")
        return

    year = st.selectbox("Choose year", options=years, index=0)
    rows = list_receipts(conn, year=int(year), status="All")
    if not rows:
        st.info("No receipts for that year.")
        return

    summary_csv = make_accountant_summary_csv(rows)
    qb_csv = make_quickbooks_csv(rows)
    pnl_csv = make_monthly_pnl_csv(rows)
    zip_bytes = make_receipts_zip_bytes(rows)

    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button(
            label=f"⬇️ Accountant summary CSV ({year})",
            data=summary_csv,
            file_name=f"bookai_{year}_accountant_summary.csv",
            mime="text/csv",
        )
        st.download_button(
            label=f"⬇️ Receipt ZIP ({year})",
            data=zip_bytes,
            file_name=f"bookai_{year}_receipts.zip",
            mime="application/zip",
        )
    with c2:
        st.download_button(
            label=f"⬇️ QuickBooks-friendly CSV ({year})",
            data=qb_csv,
            file_name=f"bookai_{year}_quickbooks.csv",
            mime="text/csv",
        )
        st.download_button(
            label=f"⬇️ Monthly P&L CSV ({year})",
            data=pnl_csv,
            file_name=f"bookai_{year}_monthly_pnl.csv",
            mime="text/csv",
        )


def admin_page(conn, coa: Dict[str, str]):
    st.subheader("Admin")
    tab1, tab2 = st.tabs(["Chart of Accounts", "Data"])

    with tab1:
        st.markdown("#### Chart of Accounts mapping")
        st.caption("Used in exports and QuickBooks-friendly CSV (category → account code).")

        edited = dict(coa)
        for cat in all_categories()[1:]:
            edited[cat] = st.text_input(f"{cat} → Account code", value=edited.get(cat, ""), key=f"coa_{cat}")

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Save COA", type="primary"):
                save_coa(edited)
                st.success("Saved COA mapping.")
        with c2:
            if st.button("Reset to default"):
                save_coa(DEFAULT_COA)
                st.success("Reset to default.")

    with tab2:
        st.warning("Danger zone: deletes the DB row and tries to delete the stored file.")
        rows = list_receipts(conn, status="All")
        if not rows:
            st.info("No receipts.")
            return
        df = pd.DataFrame(rows)
        pick = st.selectbox("Select receipt to delete (by id)", options=df["id"].tolist())
        if st.button("🗑️ Delete selected receipt", type="secondary"):
            r = delete_receipt(conn, pick)
            if not r:
                st.error("Receipt not found.")
                return
            path = r.get("file_path")
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            st.success("Deleted.")


if __name__ == "__main__":
    main()

