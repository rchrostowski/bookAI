import streamlit as st
import pandas as pd
from datetime import datetime
from pathlib import Path

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


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _is_missing_row(r: dict) -> bool:
    vendor_ok = bool((r.get("vendor") or "").strip())
    date_ok = bool((r.get("date") or "").strip())
    amt_ok = _safe_float(r.get("amount"), 0) > 0
    return not (vendor_ok and date_ok and amt_ok)


def _estimate_savings(df: pd.DataFrame) -> tuple[float, int]:
    """
    Simple, honest estimate for the dashboard.
    Assumptions (tweakable later):
      - Manual per receipt: 5 min
      - With BookIQ: auto-approved ~1 min, needs review ~3 min
    """
    if df.empty:
        return 0.0, 0

    for col, default in [("needs_review", 0), ("vendor", ""), ("date", ""), ("amount", 0.0)]:
        if col not in df.columns:
            df[col] = default

    needs_review = pd.to_numeric(df["needs_review"], errors="coerce").fillna(0).astype(int)
    auto_cnt = int((needs_review == 0).sum())
    review_cnt = int((needs_review == 1).sum())

    minutes_saved = auto_cnt * (5 - 1) + review_cnt * (5 - 3)  # 4 min + 2 min
    hours_saved = round(minutes_saved / 60.0, 1)

    # “admin cost avoided” is a story tool; keep conservative
    admin_cost_avoided = int(hours_saved * 75)  # $75/hr blended owner/bookkeeper

    return hours_saved, admin_cost_avoided


# -------------------------
# Sidebar: Workspace
# -------------------------
with st.sidebar:
    st.title("🧾 BookIQ")
    st.caption("Receipt photos → accountant-ready records")

    ws_code = st.text_input(
        "Workspace code",
        value=st.session_state.get("ws_code", ""),
        placeholder="e.g. JOES-BAR-4821",
    )
    if st.button("Enter workspace", type="primary"):
        st.session_state["ws_code"] = ws_code.strip()

    st.divider()
    st.markdown("### What happens when you upload")
    st.markdown(
        "- BookIQ reads the receipt (OCR)\n"
        "- Extracts **vendor / date / total**\n"
        "- Suggests category + account code\n"
        "- Flags only the ones that truly need review\n"
        "- Exports a clean accountant package\n"
    )

if not st.session_state.get("ws_code"):
    st.info("Enter a **workspace code** in the sidebar to begin.")
    st.stop()

WS_DIR = workspace_dir(st.session_state["ws_code"])
MEM = load_memory(WS_DIR)

# Load once for header metrics
_all_rows = list_txns(WS_DIR, include_deleted=False)
_df = pd.DataFrame(_all_rows) if _all_rows else pd.DataFrame()

hours_saved, admin_saved = _estimate_savings(_df)

st.title("BookIQ")

# -------------------------
# "UMPH" header: ROI + urgency
# -------------------------
m1, m2, m3, m4 = st.columns([1.2, 1, 1, 1])
m1.metric("Receipts processed", 0 if _df.empty else len(_df))
m2.metric("Hours saved (est.)", hours_saved)
m3.metric("Admin cost avoided (est.)", f"${admin_saved}")
# Accountant readiness score (simple)
needs_review_cnt = 0 if _df.empty or "needs_review" not in _df.columns else int(pd.to_numeric(_df["needs_review"], errors="coerce").fillna(0).astype(int).sum())
m4.metric("Needs review", needs_review_cnt)

missing_cnt = 0
if _all_rows:
    missing_cnt = sum(1 for r in _all_rows if _is_missing_row(r))

if missing_cnt > 0:
    st.warning(f"⚠️ {missing_cnt} receipt(s) are missing vendor/date/amount. These create accountant follow-ups.")
elif needs_review_cnt > 0:
    st.info("A few receipts need a quick review — approve once and BookIQ will remember them.")
else:
    st.success("Accountant-ready ✅ Everything is clean and exportable.")

st.caption("This dashboard is conservative on savings. The goal is to make your bookkeeping feel *done*, not *in progress*.")

tab_upload, tab_review, tab_browse, tab_reports, tab_export, tab_deleted = st.tabs(
    ["1) Upload", "2) Needs review", "3) Browse", "4) Reports", "5) Send to accountant", "Recently deleted"]
)

# ==========================================================
# 1) UPLOAD
# ==========================================================
with tab_upload:
    st.header("Upload a receipt")
    st.write("Upload a receipt photo (JPG/PNG) or PDF. BookIQ extracts fields, categorizes it, and gets it accountant-ready.")

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
        learned_from = int(suggestion.get("learned_from", 0) or 0)

        account_code, account_name = coa_for_category(category)

        # Trust language (not just a %)
        auto_approved = (confidence >= 0.80) and bool(vendor) and bool(date) and (amount > 0)

        colA, colB = st.columns([1, 1], gap="large")

        with colA:
            st.subheader("Preview")
            if preview_img is not None:
                st.image(preview_img, use_container_width=True)
            with st.expander("OCR text (debug)"):
                st.code(raw_text if raw_text else "(No OCR text extracted)")

        with colB:
            st.subheader("BookIQ decision")
            if auto_approved:
                st.success("Auto-approved ✅")
            else:
                st.warning("Needs review ⚠️ (approve once and BookIQ learns)")

            if learned_from > 0:
                st.caption(f"Learned from **{learned_from}** prior receipt(s) for this vendor.")

            if reasons:
                st.write("**Why:**")
                st.write("• " + "\n• ".join(reasons))

            st.divider()

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
            st.caption(f"Confidence score: **{int(confidence * 100)}%** (used only to decide what needs review)")

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

                # This is the "rules engine" / learning loop:
                remember_vendor_mapping(MEM, vendor=vendor, category=category, account_code=account_code)
                save_memory(WS_DIR, MEM)

                st.success("Saved ✅ BookIQ learned this vendor for next time.")
                st.rerun()


# ==========================================================
# 2) NEEDS REVIEW
# ==========================================================
with tab_review:
    st.header("Needs review")
    rows = list_txns(WS_DIR, include_deleted=False)
    review = [r for r in rows if int(r.get("needs_review") or 0) == 1]

    if not review:
        st.success("Nothing needs review 🎉")
        st.caption("When this stays empty, your accountant export is painless.")
    else:
        st.write("Approve these once. BookIQ will remember the vendor pattern and auto-approve next time.")

        for r in review[:200]:
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])

                with c1:
                    st.markdown(f"**{r.get('vendor','(missing vendor)')}**")
                    st.caption(f"ID: {r['id']} • Confidence: {float(r.get('confidence') or 0):.2f}")
                    st.write(f"Date: `{r.get('date','')}`  |  Amount: **${float(r.get('amount') or 0):.2f}**")
                    st.write(f"Category: `{r.get('category','Other')}`  |  Account: `{r.get('account_code','')}`")
                    st.caption("Tip: Fix it once → BookIQ learns this vendor going forward.")

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
                    if st.button("Approve + teach BookIQ", type="primary", key=f"ap_{r['id']}"):
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
                                "needs_review": 0,
                                "confidence": max(float(r.get("confidence") or 0), 0.90),
                            },
                        )

                        if new_job:
                            remember_job(MEM, new_job)

                        remember_vendor_mapping(MEM, vendor=new_vendor, category=new_cat, account_code=code)
                        save_memory(WS_DIR, MEM)

                        st.success("Approved ✅ BookIQ learned this vendor.")
                        st.rerun()

                    if st.button("Delete", key=f"del_{r['id']}"):
                        soft_delete_txn(WS_DIR, r["id"])
                        st.warning("Moved to Recently deleted 🗑️")
                        st.rerun()


# ==========================================================
# 3) BROWSE  (sort before selecting show cols)
# ==========================================================
with tab_browse:
    st.header("Browse receipts")

    rows = list_txns(WS_DIR, include_deleted=False)
    if not rows:
        st.info("No receipts yet.")
    else:
        df = pd.DataFrame(rows)

        # Backfill expected columns
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

        # Sort BEFORE selecting columns
        sort_cols = [c for c in ["date", "created_at"] if c in view.columns]
        if sort_cols:
            sorted_view = view.sort_values(
                by=sort_cols,
                ascending=[False] * len(sort_cols),
                na_position="last",
            )
        else:
            sorted_view = view

        left, right = st.columns([1.2, 0.8], gap="large")

        with left:
            st.dataframe(
                sorted_view[show_cols],
                use_container_width=True,
                hide_index=True
            )
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
                                # if they fixed it, clear review flag
                                "needs_review": 0 if (ev.strip() and ed.strip() and float(ea) > 0) else 1,
                            })
                            if ej:
                                remember_job(MEM, ej)
                            remember_vendor_mapping(MEM, vendor=ev, category=ec, account_code=code)
                            save_memory(WS_DIR, MEM)
                            st.success("Saved ✅ BookIQ learned this vendor.")
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
        st.caption("This is designed to match how an accountant thinks: monthly totals by category.")
        st.dataframe(pnl, use_container_width=True)
        st.line_chart(pnl.sum(axis=1), height=220)

        st.download_button(
            "Download Monthly P&L CSV",
            data=build_monthly_pnl_csv(pnl),
            file_name="monthly_pnl.csv",
            mime="text/csv"
        )


# ==========================================================
# 5) SEND TO ACCOUNTANT (reframed export)
# ==========================================================
with tab_export:
    st.header("Send to accountant")
    st.write("BookIQ creates a clean accountant package: **CSV + receipts ZIP**, organized and ready to upload.")

    rows = list_txns(WS_DIR, include_deleted=False)
    needs_review = [r for r in rows if int(r.get("needs_review") or 0) == 1]
    missing = [r for r in rows if _is_missing_row(r)]

    st.markdown("### Readiness checklist")
    if not rows:
        st.info("No receipts yet.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total receipts", len(rows))
        c2.metric("Needs review", len(needs_review))
        c3.metric("Missing info", len(missing))

        if len(needs_review) == 0 and len(missing) == 0:
            st.success("Accountant-ready ✅ No follow-ups expected.")
        else:
            st.warning("Not fully clean yet — fixing these reduces accountant back-and-forth.")
            if len(needs_review) > 0:
                st.write(f"• {len(needs_review)} receipt(s) need review (approve once and BookIQ learns).")
            if len(missing) > 0:
                st.write(f"• {len(missing)} receipt(s) are missing vendor/date/amount.")

    st.divider()
    st.markdown("### Build the package")

    if st.button("Build accountant package", type="primary"):
        csv_bytes, zip_bytes = build_accountant_pack(WS_DIR)
        st.download_button("Download accountant CSV", data=csv_bytes, file_name="bookiq_export.csv", mime="text/csv")
        st.download_button("Download receipts ZIP", data=zip_bytes, file_name="receipts.zip", mime="application/zip")


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


