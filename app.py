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
# Chart of Accounts (defaults)
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
# Sidebar: Workspace gate + guidance
# -------------------------
with st.sidebar:
    st.title("🧾 BookIQ")
    st.caption("Receipt → Auto-read → Review → Export")

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
        "2) BookIQ extracts **vendor/date/total** and suggests a category\n"
        "3) Fix anything in **Needs review** or **Browse**\n"
        "4) Export an **Accountant Pack** (CSV + receipt ZIP)\n"
    )
    st.markdown("### Privacy")
    st.markdown(
        "- Your data is stored **inside your workspace**\n"
        "- Anyone with the workspace code can access it (pilot mode)\n"
        "- You can delete or undo-delete receipts anytime\n"
    )

if not st.session_state.get("ws_code"):
    st.info("Enter your **workspace code** in the sidebar to continue.")
    st.stop()

WS_DIR = workspace_dir(st.session_state["ws_code"])
MEM = load_memory(WS_DIR)

st.title("BookIQ")
st.caption("A simple, accountant-friendly receipt capture + categorization tool.")


# -------------------------
# Tabs
# -------------------------
tab_upload, tab_review, tab_browse, tab_reports, tab_export, tab_deleted = st.tabs(
    ["1) Upload", "2) Needs review", "3) Browse", "4) Reports", "5) Export", "Recently deleted"]
)


# ==========================================================
# 1) UPLOAD
# ==========================================================
with tab_upload:
    st.header("Upload a receipt")

    st.write("Upload a photo (JPG/PNG) or PDF. We extract vendor/date/total, categorize it, and save it to your workspace.")

    up = st.file_uploader("Receipt file", type=["jpg", "jpeg", "png", "pdf"])

    colA, colB = st.columns([1, 1], gap="large")

    if up is not None:
        file_bytes = up.getvalue()
        preview_img, raw_text = ocr_upload(up.name, file_bytes)

        fields = extract_fields(raw_text) if raw_text else {}

        # Backward compatibility: if a tuple sneaks in, convert to dict
        if isinstance(fields, tuple) and len(fields) >= 3:
            fields = {"vendor": fields[0], "date": fields[1], "amount": fields[2]}
        if fields is None:
            fields = {}

        vendor = (fields.get("vendor") or "").strip()
        date = (fields.get("date") or "").strip()
        amount = float(fields.get("amount") or 0.0)

        # Categorize + confidence breakdown (and vendor memory)
        suggestion = categorize(raw_text or "", vendor=vendor, memory=MEM, coa=COA)
        category = suggestion["category"]
        confidence = float(suggestion["confidence"])
        reasons = suggestion.get("reasons", [])

        account_code, account_name = coa_for_category(category)

        known_jobs = get_known_jobs(MEM)

        with colA:
            st.subheader("Preview")
            st.image(preview_img, use_container_width=True)
            with st.expander("OCR text (debug)"):
                st.code(raw_text or "(No OCR text extracted)")

        with colB:
            st.subheader("Extracted fields (editable)")
            vendor = st.text_input("Vendor", value=vendor)
            date = st.text_input("Date (YYYY-MM-DD)", value=date)
            amount = st.number_input("Total amount", min_value=0.0, value=float(amount), step=0.01)

            job = st.selectbox("Job (optional)", [""] + known_jobs, index=0)
            if job == "":
                job = st.text_input("Or type a new job", value="", placeholder="Job #1042 / Smith Backyard")

            notes = st.text_area("Notes (optional)", value="", placeholder="Anything you want your accountant to see")

            category = st.selectbox("Category", options=list(COA.keys()),
                                    index=list(COA.keys()).index(category) if category in COA else list(COA.keys()).index("Other"))
            account_code, account_name = coa_for_category(category)
            st.caption(f"Chart of Accounts: **{account_code} — {account_name}**")

            st.metric("AI confidence", f"{int(confidence*100)}%")
            if reasons:
                st.caption("Why this confidence:")
                st.write("• " + "\n• ".join(reasons))

            if confidence < 0.75:
                st.warning("Low confidence → will go to **Needs review** automatically. You can still save now.")

            st.divider()
            st.subheader("Save")

            # Split receipt into multiple lines (optional)
            split = st.toggle("Split this receipt into multiple line items (advanced)", value=False)
            splits = []
            if split:
                st.info("Use this if one receipt covers multiple jobs/categories (e.g., mixed supplies).")
                n = st.number_input("How many lines?", min_value=2, max_value=10, value=2, step=1)
                remaining = amount
                for i in range(int(n)):
                    with st.container(border=True):
                        st.markdown(f"**Line {i+1}**")
                        s_amt = st.number_input(f"Amount {i+1}", min_value=0.0, value=float(remaining if i == int(n)-1 else 0.0), step=0.01, key=f"split_amt_{i}")
                        s_cat = st.selectbox(f"Category {i+1}", options=list(COA.keys()), key=f"split_cat_{i}")
                        s_code, _ = coa_for_category(s_cat)
                        s_job = st.text_input(f"Job {i+1}", value=job or "", key=f"split_job_{i}")
                        s_notes = st.text_input(f"Notes {i+1}", value="", key=f"split_notes_{i}")
                        splits.append({"amount": s_amt, "category": s_cat, "account_code": s_code, "job": s_job, "notes": s_notes})
                        remaining = max(0.0, remaining - float(s_amt))

                st.caption("Tip: You can leave the last line to auto-balance by setting it to the remaining amount.")

            if st.button("Save receipt", type="primary"):
                if split:
                    # Save as group
                    group_id = f"grp_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                    for s in splits:
                        add_txn(
                            WS_DIR,
                            date=date,
                            vendor=vendor,
                            amount=float(s["amount"]),
                            category=s["category"],
                            account_code=s["account_code"],
                            confidence=confidence,
                            confidence_notes="; ".join(reasons),
                            job=s["job"],
                            notes=s["notes"] or notes,
                            receipt_bytes=file_bytes,
                            receipt_filename=up.name,
                            group_id=group_id,
                        )
                else:
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

                # Remember job + vendor mapping (learning)
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
        st.write("These entries were flagged because confidence was low or a key field was missing.")
        for r in review[:100]:
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])

                with c1:
                    st.markdown(f"**{r.get('vendor','(missing vendor)')}**")
                    st.caption(f"ID: {r['id']} • Confidence: {float(r.get('confidence') or 0):.2f}")
                    st.write(f"Date: `{r.get('date','')}`  |  Amount: **${float(r.get('amount') or 0):.2f}**")
                    st.write(f"Category: `{r.get('category','Other')}`  |  Account: `{r.get('account_code','')}`")
                    if r.get("job"):
                        st.write(f"Job: `{r.get('job')}`")
                    if r.get("confidence_notes"):
                        with st.expander("Confidence details"):
                            st.write(r.get("confidence_notes"))

                with c2:
                    new_vendor = st.text_input("Vendor", value=r.get("vendor",""), key=f"rv_{r['id']}")
                    new_date = st.text_input("Date (YYYY-MM-DD)", value=r.get("date",""), key=f"rd_{r['id']}")
                    new_amount = st.number_input("Amount", min_value=0.0, value=float(r.get("amount") or 0), step=0.01, key=f"ra_{r['id']}")
                    new_cat = st.selectbox("Category", options=list(COA.keys()),
                                           index=list(COA.keys()).index(r.get("category","Other")) if r.get("category","Other") in COA else 0,
                                           key=f"rc_{r['id']}")
                    code, _ = coa_for_category(new_cat)

                    known_jobs = get_known_jobs(MEM)
                    new_job = st.selectbox("Job", [""] + known_jobs, index=0, key=f"rjpick_{r['id']}")
                    if new_job == "":
                        new_job = st.text_input("Or type a new job", value=r.get("job",""), key=f"rj_{r['id']}")

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
                            "approved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                            "confidence": max(float(r.get("confidence") or 0), 0.90),
                        })

                        # Learn from approval
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
# 3) BROWSE + RECEIPT DETAIL + DELETE + FILTERS
# ==========================================================
with tab_browse:
    st.header("Browse receipts")

    rows = list_txns(WS_DIR, include_deleted=False)
    if not rows:
        st.info("No receipts yet. Upload one in the Upload tab.")
    else:
        df = pd.DataFrame(rows)
        df["amount"] = df["amount"].astype(float)
        df["confidence"] = df["confidence"].astype(float)

        # Filters
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

        q = st.text_input("Search (vendor, notes, job, category)", value="")

        filtered = df.copy()
        if job_pick != "All":
            filtered = filtered[filtered["job"] == job_pick]
        if vendor_pick != "All":
            filtered = filtered[filtered["vendor"] == vendor_pick]
        if cat_pick != "All":
            filtered = filtered[filtered["category"] == cat_pick]
        filtered = filtered[filtered["confidence"] >= min_conf]
        if only_review:
            filtered = filtered[filtered["needs_review"].astype(int) == 1]
        if q.strip():
            qq = q.strip().lower()
            def _hit(row):
                blob = " ".join([
                    str(row.get("vendor","")),
                    str(row.get("notes","")),
                    str(row.get("job","")),
                    str(row.get("category","")),
                ]).lower()
                return qq in blob
            filtered = filtered[filtered.apply(_hit, axis=1)]

        # Date range filter (optional)
        with st.expander("Date range filter"):
            dmin = st.text_input("Start date (YYYY-MM-DD)", value="")
            dmax = st.text_input("End date (YYYY-MM-DD)", value="")
            if dmin.strip():
                filtered = filtered[filtered["date"] >= dmin.strip()]
            if dmax.strip():
                filtered = filtered[filtered["date"] <= dmax.strip()]

        st.caption(f"{len(filtered)} receipts shown")

        # Selection + detail panel
        left, right = st.columns([1.2, 0.8], gap="large")

        with left:
            show_cols = ["id", "date", "vendor", "amount", "category", "account_code", "job", "confidence", "needs_review"]
            st.dataframe(
                filtered[show_cols].sort_values(by=["date","created_at"], ascending=[False, False]),
                use_container_width=True,
                hide_index=True,
            )
            selected_id = st.text_input("Open receipt by ID (copy from table)", value=st.session_state.get("selected_id",""))
            st.session_state["selected_id"] = selected_id.strip()

        with right:
            st.subheader("Receipt details")
            sel = st.session_state.get("selected_id","").strip()
            if not sel:
                st.info("Paste an ID from the table to view/edit a receipt.")
            else:
                recs = {r["id"]: r for r in rows}
                r = recs.get(sel)
                if not r:
                    st.warning("ID not found in current (non-deleted) receipts.")
                else:
                    # show receipt image file if available
                    try:
                        from pathlib import Path
                        img_path = WS_DIR / (r.get("receipt_path") or "")
                        if img_path.exists():
                            st.image(str(img_path), use_container_width=True)
                        else:
                            st.caption("Receipt image file not found.")
                    except Exception:
                        st.caption("Could not render receipt image.")

                    st.caption(f"Created: {r.get('created_at','')}  |  Approved: {r.get('approved_at','') or '(not approved)'}")

                    ev = st.text_input("Vendor", value=r.get("vendor",""), key=f"edit_vendor_{sel}")
                    ed = st.text_input("Date", value=r.get("date",""), key=f"edit_date_{sel}")
                    ea = st.number_input("Amount", min_value=0.0, value=float(r.get("amount") or 0), step=0.01, key=f"edit_amount_{sel}")

                    ec = st.selectbox("Category", options=list(COA.keys()),
                                      index=list(COA.keys()).index(r.get("category","Other")) if r.get("category","Other") in COA else 0,
                                      key=f"edit_cat_{sel}")
                    code, _ = coa_for_category(ec)
                    st.caption(f"Account: **{code}**")

                    known_jobs = get_known_jobs(MEM)
                    ej_pick = st.selectbox("Job", [""] + known_jobs, index=0, key=f"edit_jobpick_{sel}")
                    if ej_pick == "":
                        ej = st.text_input("Or type job", value=r.get("job",""), key=f"edit_job_{sel}")
                    else:
                        ej = ej_pick

                    en = st.text_area("Notes", value=r.get("notes",""), key=f"edit_notes_{sel}")

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
                                "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                            })

                            # Learn from edits
                            if ej:
                                remember_job(MEM, ej)
                            remember_vendor_mapping(MEM, vendor=ev, category=ec, account_code=code)
                            save_memory(WS_DIR, MEM)

                            st.success("Saved ✅")
                            st.rerun()
                    with c2:
                        confirm = st.checkbox("Confirm delete", key=f"confirm_del_{sel}")
                        if st.button("Delete receipt", key=f"del2_{sel}") and confirm:
                            soft_delete_txn(WS_DIR, sel)
                            st.warning("Moved to Recently deleted 🗑️")
                            st.session_state["selected_id"] = ""
                            st.rerun()


# ==========================================================
# 4) REPORTS (Monthly P&L)
# ==========================================================
with tab_reports:
    st.header("Reports")

    rows = list_txns(WS_DIR, include_deleted=False)
    if not rows:
        st.info("No data yet.")
    else:
        df = pd.DataFrame(rows)
        df["amount"] = df["amount"].astype(float)

        # Month column
        df["month"] = df["date"].astype(str).str.slice(0, 7).replace("", "unknown")
        pnl = df.pivot_table(index="month", columns="category", values="amount", aggfunc="sum", fill_value=0).sort_index()

        st.subheader("Monthly expense summary (P&L-style)")
        st.dataframe(pnl, use_container_width=True)

        st.line_chart(pnl.sum(axis=1), height=220)

        csv_bytes = build_monthly_pnl_csv(pnl)
        st.download_button("Download Monthly P&L CSV", data=csv_bytes, file_name="monthly_pnl.csv", mime="text/csv")


# ==========================================================
# 5) EXPORT (Accountant Pack)
# ==========================================================
with tab_export:
    st.header("Export")

    st.write("Download a QuickBooks-friendly CSV and a ZIP of receipt images organized by month and category.")
    if st.button("Build Accountant Pack", type="primary"):
        csv_bytes, zip_bytes = build_accountant_pack(WS_DIR)
        st.download_button("Download CSV", data=csv_bytes, file_name="bookiq_export.csv", mime="text/csv")
        st.download_button("Download Receipts ZIP", data=zip_bytes, file_name="receipts.zip", mime="application/zip")


# ==========================================================
# Recently deleted (Undo + Purge)
# ==========================================================
with tab_deleted:
    st.header("Recently deleted")

    deleted = list_txns(WS_DIR, include_deleted=True, only_deleted=True)
    if not deleted:
        st.info("No deleted receipts.")
    else:
        ddf = pd.DataFrame(deleted)
        ddf["amount"] = ddf["amount"].astype(float)
        st.dataframe(ddf[["id","date","vendor","amount","category","job","deleted_at"]], use_container_width=True, hide_index=True)

        did = st.text_input("ID to restore/purge", value="")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Undo delete", type="primary") and did.strip():
                undo_delete_txn(WS_DIR, did.strip())
                st.success("Restored ✅")
                st.rerun()
        with c2:
            if st.button("Purge permanently") and did.strip():
                purge_deleted_txn(WS_DIR, did.strip())
                st.warning("Purged permanently 🧨")
                st.rerun()

