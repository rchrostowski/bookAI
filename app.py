import streamlit as st
import pandas as pd

from src.workspace import workspace_dir
from src.storage import (
    add_txn, list_txns, update_txn, delete_txn, build_accountant_pack
)

from src.ocr import ocr_upload
from src.parse import extract_fields
from src.categorize import categorize


st.set_page_config(page_title="BookIQ", page_icon="🧾", layout="wide")

# Simple Chart of Accounts mapping (edit as needed)
COA = {
    "Fuel": ("6100", "Fuel"),
    "Meals": ("6200", "Meals & Entertainment"),
    "Materials / Supplies": ("6300", "Materials & Supplies"),
    "Tools & Equipment": ("6400", "Tools & Equipment"),
    "Vehicle Maintenance": ("6500", "Vehicle Maintenance"),
    "Office / Admin": ("6600", "Office & Admin"),
    "Subcontractors": ("6700", "Subcontractors"),
    "Permits / Fees": ("6800", "Permits & Fees"),
    "Other": ("6999", "Other"),
}

def coa_for_category(cat: str):
    return COA.get(cat, COA["Other"])


# -------------------------
# Workspace Gate
# -------------------------
with st.sidebar:
    st.title("🧾 BookIQ")
    st.caption("Receipt → Auto-read → Review → Export")

    ws_code = st.text_input("Workspace code", value=st.session_state.get("ws_code", ""), placeholder="e.g. ACME-PLUMBING-4821")
    if st.button("Enter workspace", type="primary"):
        st.session_state["ws_code"] = ws_code.strip()

    st.divider()
    st.markdown("**Privacy**")
    st.markdown("- Your uploads are stored inside your workspace.\n- You can delete any receipt.\n- No bank connections.")

if not st.session_state.get("ws_code"):
    st.info("Enter your **workspace code** in the sidebar to continue.")
    st.stop()

try:
    WS_DIR = workspace_dir(st.session_state["ws_code"])
except Exception as e:
    st.error(str(e))
    st.stop()


# -------------------------
# Tabs
# -------------------------
tab_upload, tab_review, tab_browse, tab_export = st.tabs(["1) Upload", "2) Needs review", "3) Browse", "4) Export"])


# -------------------------
# 1) Upload
# -------------------------
with tab_upload:
    st.header("Upload a receipt")
    st.write("Upload a photo (JPG/PNG) or PDF. We extract vendor/date/total, categorize it, and save it to your workspace.")

    up = st.file_uploader("Receipt file", type=["jpg","jpeg","png","pdf"])

    colA, colB = st.columns([1,1], gap="large")
    if up is not None:
        file_bytes = up.getvalue()
        preview_img, raw_text = ocr_upload(up.name, file_bytes)

        fields = extract_fields(raw_text) if raw_text else {}
        vendor = (fields.get("vendor") or "").strip()
        date = (fields.get("date") or "").strip()
        amount = float(fields.get("amount") or 0)

        category, conf = categorize(raw_text or "", vendor=vendor)
        account_code, account_name = coa_for_category(category)

        with colA:
            st.subheader("Preview")
            st.image(preview_img, use_container_width=True)
            st.caption("Tip: clear, well-lit photos improve results.")

            with st.expander("OCR text (debug)"):
                st.code(raw_text or "(No OCR text extracted)")

        with colB:
            st.subheader("Extracted fields")
            vendor = st.text_input("Vendor", value=vendor)
            date = st.text_input("Date (YYYY-MM-DD)", value=date)
            amount = st.number_input("Total amount", min_value=0.0, value=float(amount), step=0.01)

            job = st.text_input("Job (optional)", value="", placeholder="e.g., Job #1042 / Smith Backyard")
            notes = st.text_area("Notes (optional)", value="", placeholder="Anything you want your accountant to see")

            # Category + COA
            category = st.selectbox("Category", options=list(COA.keys()), index=list(COA.keys()).index(category) if category in COA else list(COA.keys()).index("Other"))
            account_code, account_name = coa_for_category(category)
            st.caption(f"Chart of Accounts: **{account_code} — {account_name}**")

            # Confidence
            st.metric("AI confidence", f"{int(conf*100)}%")
            if conf < 0.75:
                st.warning("Low confidence → will go to **Needs review** automatically (you can still save).")

            if st.button("Save receipt", type="primary"):
                add_txn(
                    WS_DIR,
                    date=date,
                    vendor=vendor,
                    amount=amount,
                    category=category,
                    account_code=account_code,
                    confidence=conf,
                    job=job,
                    notes=notes,
                    receipt_bytes=file_bytes,
                    receipt_filename=up.name,
                )
                st.success("Saved ✅")


# -------------------------
# 2) Needs Review
# -------------------------
with tab_review:
    st.header("Needs review")
    rows = list_txns(WS_DIR)
    review = [r for r in rows if int(r.get("needs_review") or 0) == 1]

    if not review:
        st.success("Nothing needs review 🎉")
    else:
        st.write("These entries were flagged because confidence was low or a key field was missing.")
        for r in review[:50]:
            with st.container(border=True):
                c1, c2, c3 = st.columns([2,2,1])
                with c1:
                    st.markdown(f"**{r.get('vendor','(missing vendor)')}**")
                    st.caption(f"ID: {r['id']}  •  Confidence: {float(r.get('confidence') or 0):.2f}")
                    st.write(f"Date: `{r.get('date','')}`  |  Amount: **${float(r.get('amount') or 0):.2f}**")
                    st.write(f"Category: `{r.get('category','Other')}`  |  Account: `{r.get('account_code','')}`")
                    if r.get("job"):
                        st.write(f"Job: `{r.get('job')}`")
                with c2:
                    new_vendor = st.text_input("Vendor", value=r.get("vendor",""), key=f"rv_{r['id']}")
                    new_date = st.text_input("Date", value=r.get("date",""), key=f"rd_{r['id']}")
                    new_amount = st.number_input("Amount", min_value=0.0, value=float(r.get("amount") or 0), step=0.01, key=f"ra_{r['id']}")
                    new_cat = st.selectbox("Category", options=list(COA.keys()),
                                           index=list(COA.keys()).index(r.get("category","Other")) if r.get("category","Other") in COA else 0,
                                           key=f"rc_{r['id']}")
                    code, _ = coa_for_category(new_cat)
                    new_job = st.text_input("Job", value=r.get("job",""), key=f"rj_{r['id']}")
                    new_notes = st.text_input("Notes", value=r.get("notes",""), key=f"rn_{r['id']}")
                with c3:
                    if st.button("Approve", key=f"ap_{r['id']}", type="primary"):
                        update_txn(WS_DIR, r["id"], {
                            "vendor": new_vendor,
                            "date": new_date,
                            "amount": float(new_amount),
                            "category": new_cat,
                            "account_code": code,
                            "job": new_job,
                            "notes": new_notes,
                            "confidence": max(float(r.get("confidence") or 0), 0.90),  # approved = trusted
                        })
                        st.success("Approved ✅")

                    if st.button("Delete", key=f"del_{r['id']}"):
                        delete_txn(WS_DIR, r["id"])
                        st.warning("Deleted 🗑️")
                        st.rerun()


# -------------------------
# 3) Browse + Filters (includes JOB filter)
# -------------------------
with tab_browse:
    st.header("Browse receipts")
    rows = list_txns(WS_DIR)
    if not rows:
        st.info("No receipts yet. Upload one in the Upload tab.")
    else:
        df = pd.DataFrame(rows)
        df["amount"] = df["amount"].astype(float)
        df["confidence"] = df["confidence"].astype(float)

        # Filters
        jobs = sorted([j for j in df["job"].fillna("").unique().tolist() if j.strip()])
        vendors = sorted([v for v in df["vendor"].fillna("").unique().tolist() if v.strip()])
        cats = sorted(df["category"].fillna("Other").unique().tolist())

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            job_pick = st.selectbox("Job", ["All"] + jobs)
        with f2:
            vendor_pick = st.selectbox("Vendor", ["All"] + vendors)
        with f3:
            cat_pick = st.selectbox("Category", ["All"] + cats)
        with f4:
            min_conf = st.slider("Min confidence", 0.0, 1.0, 0.0, 0.05)

        filtered = df.copy()
        if job_pick != "All":
            filtered = filtered[filtered["job"] == job_pick]
        if vendor_pick != "All":
            filtered = filtered[filtered["vendor"] == vendor_pick]
        if cat_pick != "All":
            filtered = filtered[filtered["category"] == cat_pick]
        filtered = filtered[filtered["confidence"] >= min_conf]

        st.dataframe(
            filtered[["date","vendor","amount","category","account_code","job","confidence","needs_review"]],
            use_container_width=True,
            hide_index=True,
        )


# -------------------------
# 4) Export
# -------------------------
with tab_export:
    st.header("Export")
    st.write("Download a QuickBooks-friendly CSV and a ZIP of receipt images organized by month and category.")

    if st.button("Build Accountant Pack", type="primary"):
        csv_bytes, zip_bytes = build_accountant_pack(WS_DIR)
        st.download_button("Download CSV", data=csv_bytes, file_name="bookiq_export.csv", mime="text/csv")
        st.download_button("Download Receipts ZIP", data=zip_bytes, file_name="receipts.zip", mime="application/zip")

    st.divider()
    st.caption("Workspace storage path:")
    st.code(str(WS_DIR))


