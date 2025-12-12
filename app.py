import streamlit as st
import pandas as pd
from datetime import datetime

from src.workspace import workspace_dir
from src.storage import (
    add_txn,
    list_txns,
    update_txn,
    soft_delete_txn,
    undo_delete_txn,
    purge_deleted_txn,
    build_accountant_pack,
    build_monthly_pnl_csv,
)
from src.memory import (
    load_memory,
    save_memory,
    remember_vendor_mapping,
    remember_job,
    get_known_jobs,
)
from src.ocr import ocr_upload
from src.parse import extract_fields
from src.categorize import categorize


st.set_page_config(page_title="BookIQ", page_icon="🧾", layout="wide")

# -------------------------
# Chart of Accounts (simple mapping)
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


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# -------------------------
# Sidebar: Workspace
# -------------------------
with st.sidebar:
    st.title("🧾 BookIQ")
    st.caption("Upload receipts → auto-read → review → export")

    ws_code = st.text_input(
        "Workspace code",
        value=st.session_state.get("ws_code", ""),
        placeholder="e.g. JOES-BAR-4821",
    )
    if st.button("Enter workspace", type="primary"):
        st.session_state["ws_code"] = ws_code.strip()

    st.divider()
    st.markdown("### How it works")
    st.markdown(
        "1) Upload a receipt photo\n"
        "2) BookIQ extracts **vendor / date / total**\n"
        "3) It categorizes + assigns an account code\n"
        "4) Export CSV + receipts ZIP for your accountant\n"
    )

if not st.session_state.get("ws_code"):
    st.info("Enter a **workspace code** in the sidebar to begin.")
    st.stop()

WS_DIR = workspace_dir(st.session_state["ws_code"])
MEM = load_memory(WS_DIR)

st.title("BookIQ")
st.caption("Simple, accountant-friendly receipt capture for small businesses.")

# ✅ IMPORTANT: tab_privacy is defined here so it exists later
tab_upload, tab_review, tab_browse, tab_reports, tab_export, tab_deleted, tab_privacy = st.tabs(
    ["1) Upload", "2) Needs review", "3) Browse", "4) Reports", "5) Export", "Recently deleted", "Privacy"]
)

# ==========================================================
# 1) UPLOAD
# ==========================================================
with tab_upload:
    st.header("Upload a receipt")
    st.write("Upload a receipt photo (JPG/PNG) or PDF. We'll extract fields, categorize, and save it.")

    up = st.file_uploader("Receipt file", type=["jpg", "jpeg", "png", "pdf"])

    if up is None:
        st.info("Upload a file to begin.")
    else:
        file_bytes = up.getvalue()

        # Run OCR once per file to avoid re-running on every Streamlit rerun
        key = f"ocr::{up.name}::{len(file_bytes)}"
        if st.session_state.get("ocr_key") != key:
            preview_img, raw_text = ocr_upload(up.name, file_bytes)
            st.session_state["ocr_key"] = key
            st.session_state["preview_img"] = preview_img
            st.session_state["raw_text"] = raw_text

        preview_img = st.session_state.get("preview_img")
        raw_text = st.session_state.get("raw_text") or ""

        fields = extract_fields(raw_text) if raw_text else {"vendor": "", "date": "", "amount": 0.0}

        vendor = (fields.get("vendor") or "").strip()
        date = (fields.get("date") or "").strip()
        amount = float(fields.get("amount") or 0.0)

        suggestion = categorize(raw_text, vendor=vendor, memory=MEM)
        category = suggestion.get("category", "Other")
        confidence = float(suggestion.get("confidence", 0.35))
        reasons = suggestion.get("reasons", [])

        account_code, account_name = coa_for_category(category)

        colA, colB = st.columns([1, 1], gap="large")

        with colA:
            st.subheader("Preview")
            if preview_img is not None:
                st.image(preview_img, use_container_width=True)
            with st.expander("OCR text (debug)"):
                st.code(raw_text if raw_text else "(No OCR text extracted)")

        with colB:
            st.subheader("Extracted fields (editable)")
            vendor = st.text_input("Vendor", value=vendor)
            date = st.text_input("Date (YYYY-MM-DD)", value=date)
            amount = st.number_input("Total amount", min_value=0.0, value=float(amount), step=0.01)

            known_jobs = get_known_jobs(MEM)
            job_pick = st.selectbox("Job (optional)", [""] + known_jobs, index=0)
            if job_pick == "":
                job = st.text_input("Or type a new job", value="", placeholder="Job #1042 / Smith Backyard")
            else:
                job = job_pick

            notes = st.text_area("Notes (optional)", value="", placeholder="Anything your accountant should know")

            category = st.selectbox(
                "Category",
                options=list(COA.keys()),
                index=list(COA.keys()).index(category) if category in COA else list(COA.keys()).index("Other"),
            )
            account_code, account_name = coa_for_category(category)
            st.caption(f"Account: **{account_code} — {account_name}**")

            st.metric("AI confidence", f"{int(confidence * 100)}%")
            if reasons:
                st.caption("Why:")
                st.write("• " + "\n• ".join(reasons))

            if st.button("Save receipt", type="primary"):
                add_txn(
                    WS_DIR,
                    date=date,
                    vendor=vendor,
                    amount=float(amount),
                    category=category,
                    account_code=account_code,
                    confidence=confidence,
                    confidence_notes="; ".join(reasons),
                    job=job,
                    notes=notes,
                    receipt_bytes=file_bytes,
                    receipt_filename=up.name,
                )

                if job:
                    remember_job(MEM, job)
                remember_vendor_mapping(MEM, vendor=vendor, category=category, account_code=account_code)
                save_memory(WS_DIR, MEM)

                st.success("Saved ✅")

# ==========================================================
# 2) NEEDS REVIEW
# ==========================================================
with tab_review:
    st.header("Needs review")

    rows = list_txns(WS_DIR, include_deleted=False)
    review = [r for r in rows if int(r.get("needs_review") or 0) == 1]

    if not review:
        st.success("Nothing needs review 🎉")
    else:
        st.write("These receipts need a quick check (missing fields or low confidence).")

        for r in review[:200]:
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])

                with c1:
                    st.markdown(f"**{r.get('vendor','(missing vendor)')}**")
                    st.caption(f"ID: {r['id']} • Confidence: {float(r.get('confidence') or 0):.2f}")
                    st.write(f"Date: `{r.get('date','')}`  |  Amount: **${float(r.get('amount') or 0):.2f}**")
                    st.write(f"Category: `{r.get('category','Other')}`  |  Account: `{r.get('account_code','')}`")

                with c2:
                    new_vendor = st.text_input("Vendor", value=r.get("vendor", ""), key=f"rv_{r['id']}")
                    new_date = st.text_input("Date", value=r.get("date", ""), key=f"rd_{r['id']}")
                    new_amount = st.number_input(
                        "Amount",
                        min_value=0.0,
                        value=float(r.get("amount") or 0),
                        step=0.01,
                        key=f"ra_{r['id']}",
                    )

                    new_cat = st.selectbox(
                        "Category",
                        options=list(COA.keys()),
                        index=list(COA.keys()).index(r.get("category", "Other"))
                        if r.get("category", "Other") in COA
                        else 0,
                        key=f"rc_{r['id']}",
                    )
                    code, _ = coa_for_category(new_cat)

                    known_jobs = get_known_jobs(MEM)
                    new_job_pick = st.selectbox("Job", [""] + known_jobs, index=0, key=f"rjpick_{r['id']}")
                    if new_job_pick == "":
                        new_job = st.text_input("Or type job", value=r.get("job", ""), key=f"rj_{r['id']}")
                    else:
                        new_job = new_job_pick

                    new_notes = st.text_input("Notes", value=r.get("notes", ""), key=f"rn_{r['id']}")

                with c3:
                    if st.button("Approve", type="primary", key=f"ap_{r['id']}"):
                        update_txn(
                            WS_DIR,
                            r["id"],
                            {
                                "vendor": new_vendor,
                                "date": new_date,
                                "amount": float(new_amount),
                                "category": new_cat,
                                "account_code": code,
                                "job": new_job,
                                "notes": new_notes,
                                "approved_at": _utc_now(),
                                "confidence": max(float(r.get("confidence") or 0), 0.90),
                                "needs_review": 0,
                            },
                        )

                        if new_job:
                            remember_job(MEM, new_job)
                        remember_vendor_mapping(MEM, vendor=new_vendor, category=new_cat, account_code=code)
                        save_memory(WS_DIR, MEM)

                        st.success("Approved ✅")
                        st.rerun()

                    if st.button("Delete", key=f"del_{r['id']}"):
                        soft_delete_txn(WS_DIR, r["id"])
                        st.warning("Moved to Recently deleted 🗑️")
                        st.rerun()

# ==========================================================
# 3) BROWSE
# ==========================================================
with tab_browse:
    st.header("Browse receipts")

    rows = list_txns(WS_DIR, include_deleted=False)
    if not rows:
        st.info("No receipts yet.")
    else:
        df = pd.DataFrame(rows)

        defaults = {
            "id": "",
            "date": "",
            "created_at": "",
            "vendor": "",
            "amount": 0.0,
            "category": "Other",
            "account_code": "",
            "job": "",
            "notes": "",
            "confidence": 0.0,
            "needs_review": 0,
        }
        for col, default in defaults.items():
            if col not in df.columns:
                df[col] = default

        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").fillna(0.0)
        df["needs_review"] = pd.to_numeric(df["needs_review"], errors="coerce").fillna(0).astype(int)

        jobs = sorted([j for j in df["job"].fillna("").unique().tolist() if str(j).strip()])
        vendors = sorted([v for v in df["vendor"].fillna("").unique().tolist() if str(v).strip()])
        cats = sorted(df["category"].fillna("Other").unique().tolist())

        f1, f2, f3, f4, f5 = st.columns([1, 1, 1, 1, 1])
        with f1:
            job_pick = st.selectbox("Job", ["All"] + jobs)
        with f2:
            vendor_pick = st.selectbox("Vendor", ["All"] + vendors)
        with f3:
            cat_pick = st.selectbox("Category", ["All"] + cats)
        with f4:
            min_conf = st.slider("Min confidence", 0.0, 1.0, 0.0, 0.05)
        with f5:
            only_review = st.toggle("Needs review only", value=False)

        q = st.text_input("Search (vendor/notes/job/category)", value="").strip().lower()

        view = df.copy()
        if job_pick != "All":
            view = view[view["job"] == job_pick]
        if vendor_pick != "All":
            view = view[view["vendor"] == vendor_pick]
        if cat_pick != "All":
            view = view[view["category"] == cat_pick]
        view = view[view["confidence"] >= min_conf]
        if only_review:
            view = view[view["needs_review"] == 1]

        if q:
            def _hit(r):
                blob = " ".join([
                    str(r.get("vendor", "")),
                    str(r.get("notes", "")),
                    str(r.get("job", "")),
                    str(r.get("category", "")),
                ]).lower()
                return q in blob

            view = view[view.apply(_hit, axis=1)]

        st.caption(f"{len(view)} receipts shown")

        show_cols = ["id", "date", "vendor", "amount", "category", "account_code", "job", "confidence", "needs_review"]

        sort_cols = [c for c in ["date", "created_at"] if c in view.columns]
        if sort_cols:
            view = view.sort_values(by=sort_cols, ascending=[False] * len(sort_cols), na_position="last")

        left, right = st.columns([1.2, 0.8], gap="large")

        with left:
            st.dataframe(view[show_cols], use_container_width=True, hide_index=True)
            selected_id = st.text_input("Open receipt by ID (copy from table)", value=st.session_state.get("selected_id", ""))
            st.session_state["selected_id"] = selected_id.strip()

        with right:
            st.subheader("Receipt details")
            sel = st.session_state.get("selected_id", "").strip()
            if not sel:
                st.info("Paste an ID to view/edit.")
            else:
                rec_map = {r["id"]: r for r in rows}
                r = rec_map.get(sel)
                if not r:
                    st.warning("ID not found (or deleted).")
                else:
                    receipt_path = r.get("receipt_path") or ""
                    p = WS_DIR / receipt_path
                    if receipt_path and p.exists():
                        st.image(str(p), use_container_width=True)
                    else:
                        st.caption("Receipt image not found.")

                    ev = st.text_input("Vendor", value=r.get("vendor", ""), key=f"ev_{sel}")
                    ed = st.text_input("Date", value=r.get("date", ""), key=f"ed_{sel}")
                    ea = st.number_input("Amount", min_value=0.0, value=float(r.get("amount") or 0), step=0.01, key=f"ea_{sel}")

                    ec = st.selectbox(
                        "Category",
                        options=list(COA.keys()),
                        index=list(COA.keys()).index(r.get("category", "Other")) if r.get("category", "Other") in COA else 0,
                        key=f"ec_{sel}",
                    )
                    code, _ = coa_for_category(ec)
                    st.caption(f"Account: **{code}**")

                    known_jobs = get_known_jobs(MEM)
                    ej_pick = st.selectbox("Job", [""] + known_jobs, index=0, key=f"ejpick_{sel}")
                    if ej_pick == "":
                        ej = st.text_input("Or type job", value=r.get("job", ""), key=f"ej_{sel}")
                    else:
                        ej = ej_pick

                    en = st.text_area("Notes", value=r.get("notes", ""), key=f"en_{sel}")

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Save changes", type="primary", key=f"save_{sel}"):
                            update_txn(WS_DIR, sel, {
                                "vendor": ev,
                                "date": ed,
                                "amount": float(ea),
                                "category": ec,
                                "account_code": code,
                                "job": ej,
                                "notes": en,
                                "updated_at": _utc_now(),
                                "needs_review": 0 if (ev.strip() and ed.strip() and float(ea) > 0) else 1,
                            })
                            if ej:
                                remember_job(MEM, ej)
                            remember_vendor_mapping(MEM, vendor=ev, category=ec, account_code=code)
                            save_memory(WS_DIR, MEM)
                            st.success("Saved ✅")
                            st.rerun()

                    with c2:
                        confirm = st.checkbox("Confirm delete", key=f"confirm_{sel}")
                        if st.button("Delete receipt", key=f"delete_{sel}") and confirm:
                            soft_delete_txn(WS_DIR, sel)
                            st.warning("Moved to Recently deleted 🗑️")
                            st.session_state["selected_id"] = ""
                            st.rerun()

# ==========================================================
# 4) REPORTS
# ==========================================================
with tab_reports:
    st.header("Reports")

    rows = list_txns(WS_DIR, include_deleted=False)
    if not rows:
        st.info("No receipts yet.")
    else:
        df = pd.DataFrame(rows)
        for col, default in [("date", ""), ("category", "Other"), ("amount", 0.0)]:
            if col not in df.columns:
                df[col] = default

        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        df["month"] = df["date"].astype(str).str.slice(0, 7)

        pnl = df.pivot_table(index="month", columns="category", values="amount", aggfunc="sum", fill_value=0).sort_index()

        st.subheader("Monthly expense summary (P&L-style)")
        st.dataframe(pnl, use_container_width=True)
        st.line_chart(pnl.sum(axis=1), height=220)

        st.download_button(
            "Download Monthly P&L CSV",
            data=build_monthly_pnl_csv(pnl),
            file_name="monthly_pnl.csv",
            mime="text/csv"
        )

# ==========================================================
# 5) EXPORT
# ==========================================================
with tab_export:
    st.header("Export")
    st.write("Build a CSV + receipt ZIP organized by month/category.")

    if st.button("Build Accountant Pack", type="primary"):
        csv_bytes, zip_bytes = build_accountant_pack(WS_DIR)
        st.download_button("Download CSV", data=csv_bytes, file_name="bookiq_export.csv", mime="text/csv")
        st.download_button("Download Receipts ZIP", data=zip_bytes, file_name="receipts.zip", mime="application/zip")

# ==========================================================
# Recently deleted
# ==========================================================
with tab_deleted:
    st.header("Recently deleted")

    deleted = list_txns(WS_DIR, include_deleted=True, only_deleted=True)
    if not deleted:
        st.info("No deleted receipts.")
    else:
        ddf = pd.DataFrame(deleted)
        for col, default in [("amount", 0.0), ("deleted_at", ""), ("vendor", ""), ("date", ""), ("category", "Other"), ("job", "")]:
            if col not in ddf.columns:
                ddf[col] = default
        ddf["amount"] = pd.to_numeric(ddf["amount"], errors="coerce").fillna(0.0)

        st.dataframe(
            ddf[["id", "date", "vendor", "amount", "category", "job", "deleted_at"]],
            use_container_width=True,
            hide_index=True
        )

        did = st.text_input("ID to restore/purge", value="").strip()
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Undo delete", type="primary") and did:
                undo_delete_txn(WS_DIR, did)
                st.success("Restored ✅")
                st.rerun()
        with c2:
            if st.button("Purge permanently") and did:
                purge_deleted_txn(WS_DIR, did)
                st.warning("Purged permanently 🧨")
                st.rerun()

# ==========================================================
# PRIVACY POLICY
# ==========================================================
with tab_privacy:
    st.header("Privacy Policy")
    st.caption("Last updated: " + datetime.utcnow().strftime("%B %d, %Y"))

    st.markdown(
        """
### Overview

BookIQ is built to help small businesses organize receipts and prepare accountant-ready records.
We take privacy seriously and collect **only the data required to provide this service**.

We do **not** sell, share, rent, or monetize user data in any way.

---

### What Data BookIQ Stores

BookIQ stores only data that users explicitly upload or create, including:

- Receipt images or PDFs you upload
- Extracted receipt details (vendor, date, total amount)
- Categories, account codes, and job assignments
- Optional notes added by the user
- Timestamps related to receipt creation, approval, or deletion

BookIQ does **not** store:

- Bank account credentials
- Credit card numbers
- Login passwords
- Government IDs
- Location tracking data
- Personal identity profiles

---

### How Your Data Is Used

Your data is used **only** to:

- Display receipts inside your workspace
- Automatically categorize and organize receipts
- Generate accountant-ready CSV exports
- Build organized receipt ZIP files
- Improve categorization accuracy *within your workspace only*

Your data is **never**:

- Sold or licensed
- Shared with advertisers
- Used to train external AI models
- Viewed or reviewed by humans
- Combined with data from other workspaces

---

### AI & Automation

BookIQ uses automated software to extract text and suggest categories.

Important clarifications:

- Learning happens **per workspace**
- No receipt data is shared across businesses
- No human reviews your receipts
- No uploaded data is used for external AI training

---

### Data Storage & Retention

- Uploaded data is stored securely and isolated by workspace
- Deleted receipts are soft-deleted and can be restored
- Users may permanently purge deleted receipts at any time
- Users may export all data whenever needed

If you stop using BookIQ, your data remains inactive unless explicitly deleted.

---

### Security Notice

BookIQ uses standard security practices to protect uploaded data.
However, BookIQ is an early-stage product and should not be used
to store highly sensitive personal or financial information beyond receipts.

---

### Your Control

You are always in control of your data:

- Upload only what you choose
- Edit or delete receipts at any time
- Export your data without restriction
- Permanently remove deleted data

---

### Policy Updates

If this policy changes, the updated version will always be available inside the app.
Continued use of BookIQ implies acceptance of the current policy.

---


"""
    )



