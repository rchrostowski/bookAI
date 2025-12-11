from __future__ import annotations

import os
import pandas as pd
import streamlit as st

from src.utils import ensure_dir, new_id, now_iso, safe_filename
from src.db import connect, init_db, insert_receipt, list_receipts, get_distinct, get_years, delete_receipt
from src.ocr import ocr_upload
from src.parse import extract_fields
from src.categorize import categorize, all_categories
from src.export import make_csv_bytes, make_receipts_zip_bytes

APP_TITLE = "BookAI MVP (Free)"
DATA_DIR = "data"
RECEIPTS_DIR = os.path.join(DATA_DIR, "receipts")
DB_PATH = os.path.join(DATA_DIR, "bookai.sqlite")

def setup():
    ensure_dir(DATA_DIR)
    ensure_dir(RECEIPTS_DIR)
    conn = connect(DB_PATH)
    init_db(conn)
    return conn

def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Upload receipts → OCR → auto-categorize → store → export (100% free, local).")

    conn = setup()

    page = st.sidebar.radio("Go to", ["Upload", "Receipt Library", "Exports", "Admin"], index=0)

    if page == "Upload":
        upload_page(conn)
    elif page == "Receipt Library":
        library_page(conn)
    elif page == "Exports":
        exports_page(conn)
    elif page == "Admin":
        admin_page(conn)

def upload_page(conn):
    st.subheader("1) Upload Receipt")

    uploaded = st.file_uploader(
        "Upload a receipt image (JPG/PNG) or a PDF (first page used)",
        type=["png", "jpg", "jpeg", "pdf"],
        accept_multiple_files=False,
    )

    if not uploaded:
        st.info("Upload a file to begin.")
        return

    file_bytes = uploaded.getvalue()
    file_name = uploaded.name

    with st.spinner("Running OCR (Tesseract)…"):
        try:
            preview_img, raw_text = ocr_upload(file_name, file_bytes)
        except Exception as e:
            st.error(f"OCR failed: {e}")
            st.stop()

    colA, colB = st.columns([1, 1])

    with colA:
        st.markdown("**Preprocessed image (used for OCR):**")
        st.image(preview_img, use_container_width=True)

    vendor, receipt_date, amount = extract_fields(raw_text)
    cat, conf = categorize(raw_text, vendor=vendor)

    with colB:
        st.markdown("**Auto-extracted fields (edit before saving):**")
        vendor_edit = st.text_input("Vendor", value=vendor)
        date_edit = st.text_input("Date (YYYY-MM-DD)", value=receipt_date or "")
        amount_edit = st.number_input("Amount", value=float(amount) if amount is not None else 0.0, step=0.01)
        category_edit = st.selectbox("Category", options=all_categories()[1:], index=max(0, all_categories()[1:].index(cat)) if cat in all_categories() else 0)
        st.metric("Confidence", f"{conf:.0%}")

        st.markdown("**Raw OCR text:**")
        st.text_area("", value=raw_text, height=220)

    if st.button("✅ Save receipt", type="primary"):
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
            "vendor": vendor_edit.strip(),
            "receipt_date": date_edit.strip(),
            "amount": float(amount_edit) if amount_edit is not None else None,
            "category": category_edit,
            "confidence": float(conf),
            "raw_text": raw_text,
        }
        insert_receipt(conn, row)
        st.success("Saved! Go to Receipt Library to view it.")

def library_page(conn):
    st.subheader("2) Receipt Library")

    vendors = ["All"] + get_distinct(conn, "vendor")
    years = ["All"] + get_years(conn)
    cats = all_categories()

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        year_sel = st.selectbox("Year", options=years, index=0)
    with c2:
        cat_sel = st.selectbox("Category", options=cats, index=0)
    with c3:
        vendor_sel = st.selectbox("Vendor", options=vendors, index=0)
    with c4:
        search = st.text_input("Search (vendor, filename, OCR text)")

    year_int = None if year_sel == "All" else int(year_sel)
    rows = list_receipts(
        conn,
        year=year_int,
        category=cat_sel,
        vendor=vendor_sel,
        search=search.strip() if search else None
    )

    if not rows:
        st.info("No receipts match your filters yet.")
        return

    df = pd.DataFrame(rows)
    # show friendly columns first
    display_cols = ["receipt_date", "vendor", "amount", "category", "confidence", "uploaded_at", "original_filename", "id"]
    for c in display_cols:
        if c not in df.columns:
            df[c] = None

    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("**View a receipt**")
    pick = st.selectbox("Select receipt by id", options=df["id"].tolist())
    r = next((x for x in rows if x["id"] == pick), None)
    if r:
        col1, col2 = st.columns([1, 1])
        with col1:
            st.write(f"**Vendor:** {r.get('vendor','')}")
            st.write(f"**Date:** {r.get('receipt_date','')}")
            st.write(f"**Amount:** {r.get('amount','')}")
            st.write(f"**Category:** {r.get('category','')}")
            st.write(f"**Confidence:** {r.get('confidence','')}")
            st.write(f"**Filename:** {r.get('original_filename','')}")
        with col2:
            path = r.get("file_path")
            if path and os.path.exists(path):
                ext = os.path.splitext(path)[1].lower()
                if ext == ".pdf":
                    st.info("PDF stored. (Preview is OCR’d from first page.)")
                else:
                    st.image(path, use_container_width=True)
            else:
                st.warning("Receipt file not found on disk.")

        with st.expander("Raw OCR Text"):
            st.text(r.get("raw_text", ""))

def exports_page(conn):
    st.subheader("3) Exports")

    years = get_years(conn)
    if not years:
        st.info("No receipts yet.")
        return

    year_sel = st.selectbox("Choose year to export", options=years, index=0)
    rows = list_receipts(conn, year=int(year_sel))

    if not rows:
        st.info("No receipts for that year.")
        return

    csv_bytes = make_csv_bytes(rows)
    zip_bytes = make_receipts_zip_bytes(rows)

    st.download_button(
        label=f"⬇️ Download CSV Summary ({year_sel})",
        data=csv_bytes,
        file_name=f"bookai_{year_sel}_summary.csv",
        mime="text/csv",
    )

    st.download_button(
        label=f"⬇️ Download Receipts ZIP ({year_sel})",
        data=zip_bytes,
        file_name=f"bookai_{year_sel}_receipts.zip",
        mime="application/zip",
    )

    st.caption("Tip: send the CSV + ZIP to your accountant as your year-end pack.")

def admin_page(conn):
    st.subheader("Admin")

    st.warning("This deletes receipts from the database (and tries to delete stored files). Use carefully.")

    rows = list_receipts(conn)
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

