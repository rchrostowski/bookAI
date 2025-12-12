import streamlit as st
import pandas as pd

from src.workspace import workspace_dir
from src.storage import (
    add_txn,
    list_txns,
    update_txn,
    delete_txn,
    build_accountant_pack,
)

from src.ocr import ocr_upload
from src.parse import extract_fields
from src.categorize import categorize


# -------------------------
# Page setup
# -------------------------
st.set_page_config(
    page_title="BookIQ",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 BookIQ")
st.caption("Upload receipts → auto-read → review → export for your accountant")

# -------------------------
# Chart of Accounts
# -------------------------
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
# Workspace gate
# -------------------------
with st.sidebar:
    st.header("Workspace")
    ws_code = st.text_input(
        "Workspace code",
        value=st.session_state.get("ws_code", ""),
        placeholder="e.g. JOES-BAR-4821",
    )

    if st.button("Enter workspace", type="primary"):
        st.session_state["ws_code"] = ws_code.strip()

    st.divider()
    st.markdown("**Privacy**")
    st.markdown(
        "- Your receipts are stored only in your workspace\n"
        "- You can delete anything anytime\n"
        "- No bank connections"
    )

if not st.session_state.get("ws_code"):
    st.info("Enter your **workspace code** in the sidebar to begin.")
    st.stop()

WS_DIR = workspace_dir(st.session_state["ws_code"])


# -------------------------
# Tabs
# -------------------------
tab_upload, tab_review, tab_browse, tab_export = st.tabs(
    ["1) Upload", "2) Needs review", "3) Browse", "4) Export"]
)


# ==========================================================
# 1) UPLOAD
# ==========================================================
with tab_upload:
    st.subheader("Upload a receipt")

    uploaded = st.file_uploader(
        "Upload a receipt photo (JPG/PNG) or PDF",
        type=["jpg", "jpeg", "png", "pdf"],
    )

    if uploaded:
        file_bytes = uploaded.getvalue()

        preview_img, raw_text = ocr_upload(uploaded.name, file_bytes)

        # -------------------------
        # SAFE extraction (tuple OR dict)
        # -------------------------
        fields = extract_fields(raw_text) if raw_text else {}

        # 🔒 HARD SAFETY PATCH
        if isinstance(fields, tuple) and len(fields) >= 3:
            vendor_t, date_t, amount_t = fields[0], fields[1], fields[2]
            fields = {
                "vendor": vendor_t,
                "date": date_t,
                "amount": amount_t,
            }

        if fields is None:
            fields = {}

        vendor = (fields.get("vendor") or "").strip()
        date = (fields.get("date") or "").strip()
        amount = float(fields.get("amount") or 0.0)

        category, confidence = categorize(raw_text or "", vendor=vendor)
        account_code, account_name = coa_for_category(category)

        col1, col2 = st.columns([1, 1], gap="large")

        with col1:
            st.image(preview_img, use_container_width=True)

            with st.expander("OCR text (debug)"):
                st.code(raw_text or "(no OCR text)")

        with col2:
            st.subheader("Details")

            vendor = st.text_input("Vendor", value=vendor)
            date = st.text_input("Date (YYYY-MM-DD)", value=date)
            amount = st.number_input("Total amount", value=float(amount), step=0.01)

            job = st.text_input("Job (optional)", placeholder="Job #1042 / Smith Backyard")
            notes = st.text_area("Notes (optional)")

            category = st.selectbox(
                "Category",
                options=list(COA.keys()),
                index=list(COA.keys()).index(category)
                if category in COA else list(COA.keys()).index("Other"),
            )

            account_code, account_name = coa_for_category(category)
            st.caption(f"Account: **{account_code} — {account_name}**")

            st.metric("AI confidence", f"{int(confidence * 100)}%")

            if confidence < 0.75:
                st.warning("Low confidence → will be flagged for review")

            if st.button("Save receipt", type="primary"):
                add_txn(
                    WS_DIR,
                    date=date,
                    vendor=vendor,
                    amount=amount,
                    category=category,
                    account_code=account_code,
                    confidence=confidence,
                    job=job,
                    notes=notes,
                    receipt_bytes=file_bytes,
                    receipt_filename=uploaded.name,
                )
                st.success("Receipt saved ✅")


# ==========================================================
# 2) NEEDS REVIEW
# ==========================================================
with tab_review:
    st.subheader("Needs review")

    rows = list_txns(WS_DIR)
    review = [r for r in rows if int(r.get("needs_review", 0)) == 1]

    if not review:
        st.success("Nothing needs review 🎉")
    else:
        for r in review:
            with st.container(border=True):
                st.markdown(f"**{r.get('vendor','(missing vendor)')}**")
                st.caption(f"Confidence: {float(r.get('confidence') or 0):.2f}")

                colA, colB, colC = st.columns([2, 2, 1])

                with colA:
                    new_vendor = st.text_input("Vendor", r.get("vendor",""), key=f"v{r['id']}")
                    new_date = st.text_input("Date", r.get("date",""), key=f"d{r['id']}")
                    new_amount = st.number_input(
                        "Amount", value=float(r.get("amount") or 0),
                        step=0.01, key=f"a{r['id']}"
                    )

                with colB:
                    new_cat = st.selectbox(
                        "Category", list(COA.keys()),
                        index=list(COA.keys()).index(r.get("category","Other")),
                        key=f"c{r['id']}"
                    )
                    code, _ = coa_for_category(new_cat)
                    new_job = st.text_input("Job", r.get("job",""), key=f"j{r['id']}")

                with colC:
                    if st.button("Approve", key=f"ok{r['id']}"):
                        update_txn(
                            WS_DIR,
                            r["id"],
                            {
                                "vendor": new_vendor,
                                "date": new_date,
                                "amount": new_amount,
                                "category": new_cat,
                                "account_code": code,
                                "job": new_job,
                                "confidence": 0.95,
                            },
                        )
                        st.success("Approved")
                        st.rerun()

                    if st.button("Delete", key=f"del{r['id']}"):
                        delete_txn(WS_DIR, r["id"])
                        st.warning("Deleted")
                        st.rerun()


# ==========================================================
# 3) BROWSE + JOB FILTER
# ==========================================================
with tab_browse:
    st.subheader("Browse receipts")

    rows = list_txns(WS_DIR)
    if not rows:
        st.info("No receipts yet.")
    else:
        df = pd.DataFrame(rows)
        df["amount"] = df["amount"].astype(float)
        df["confidence"] = df["confidence"].astype(float)

        jobs = sorted([j for j in df["job"].fillna("").unique() if j])
        job_pick = st.selectbox("Filter by job", ["All"] + jobs)

        if job_pick != "All":
            df = df[df["job"] == job_pick]

        st.dataframe(
            df[["date","vendor","amount","category","account_code","job","confidence"]],
            use_container_width=True,
            hide_index=True,
        )


# ==========================================================
# 4) EXPORT
# ==========================================================
with tab_export:
    st.subheader("Export for accountant")

    if st.button("Build Accountant Pack", type="primary"):
        csv_bytes, zip_bytes = build_accountant_pack(WS_DIR)

        st.download_button(
            "Download CSV",
            data=csv_bytes,
            file_name="bookiq_export.csv",
            mime="text/csv",
        )

        st.download_button(
            "Download Receipts ZIP",
            data=zip_bytes,
            file_name="receipts.zip",
            mime="application/zip",
        )


