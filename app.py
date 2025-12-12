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


def _needs_review(vendor: str, date: str, amount: float, confidence: float) -> int:
    vendor_ok = bool((vendor or "").strip())
    date_ok = bool((date or "").strip())
    amt_ok = float(amount or 0) > 0
    # If any required field missing OR confidence low → needs review
    if not (vendor_ok and date_ok and amt_ok):
        return 1
    if float(confidence or 0) < 0.72:
        return 1
    return 0


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x, default=0) -> int:
    try:
        return int(float(x))
    except Exception:
        return int(default)


def _make_df(rows):
    df = pd.DataFrame(rows or [])
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
        "deleted_at": "",
        "receipt_path": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce").fillna(0.0)
    df["needs_review"] = pd.to_numeric(df["needs_review"], errors="coerce").fillna(0).astype(int)
    df["date"] = df["date"].fillna("").astype(str)
    df["vendor"] = df["vendor"].fillna("").astype(str)
    df["category"] = df["category"].fillna("Other").astype(str)
    df["job"] = df["job"].fillna("").astype(str)
    df["notes"] = df["notes"].fillna("").astype(str)
    return df


def _duplicate_hint(df: pd.DataFrame, vendor: str, date: str, amount: float) -> pd.DataFrame:
    """Simple duplicate detector: same vendor+date and amount within 1 cent."""
    if df is None or df.empty:
        return pd.DataFrame()
    v = (vendor or "").strip().lower()
    d = (date or "").strip()
    a = float(amount or 0.0)
    if not v or not d or a <= 0:
        return pd.DataFrame()
    tmp = df.copy()
    tmp["_v"] = tmp["vendor"].fillna("").astype(str).str.strip().str.lower()
    tmp["_d"] = tmp["date"].fillna("").astype(str).str.strip()
    tmp["_a"] = pd.to_numeric(tmp["amount"], errors="coerce").fillna(0.0)
    dup = tmp[(tmp["_v"] == v) & (tmp["_d"] == d) & (tmp["_a"].sub(a).abs() <= 0.01)]
    return dup[["id", "date", "vendor", "amount", "category", "job"]].head(10)


# -------------------------
# Sidebar: Workspace + quick stats
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

# Load rows once per run (and reuse everywhere)
ROWS = list_txns(WS_DIR, include_deleted=False)
DF = _make_df(ROWS)

st.title("BookIQ")
st.caption("Simple, accountant-friendly receipt capture for small businesses.")

# Tabs (includes the missing “umph” stuff: dashboard + bulk tools)
tab_dash, tab_upload, tab_review, tab_browse, tab_reports, tab_export, tab_deleted, tab_privacy = st.tabs(
    ["0) Dashboard", "1) Upload", "2) Needs review", "3) Browse", "4) Reports", "5) Export", "Recently deleted", "Privacy"]
)

# ==========================================================
# 0) DASHBOARD (the “umph”)
# ==========================================================
with tab_dash:
    st.header("Dashboard")

    if DF.empty:
        st.info("No receipts yet. Go to **Upload** to add your first one.")
    else:
        # High-level stats
        today = datetime.utcnow().strftime("%Y-%m-%d")
        this_month = today[:7]

        total_spend = float(DF["amount"].sum())
        month_spend = float(DF[DF["date"].astype(str).str.startswith(this_month)]["amount"].sum())
        needs_review_ct = int(DF["needs_review"].sum())
        receipt_ct = int(len(DF))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total spend", f"${total_spend:,.2f}")
        c2.metric("This month", f"${month_spend:,.2f}")
        c3.metric("Receipts", f"{receipt_ct}")
        c4.metric("Needs review", f"{needs_review_ct}")

        st.divider()

        left, right = st.columns([1.2, 0.8], gap="large")

        with left:
            st.subheader("Top categories (all time)")
            cat = (
                DF.groupby("category", dropna=False)["amount"]
                .sum()
                .sort_values(ascending=False)
                .reset_index()
            )
            st.dataframe(cat, use_container_width=True, hide_index=True)

            st.subheader("Spend over time")
            tmp = DF.copy()
            tmp["month"] = tmp["date"].astype(str).str.slice(0, 7)
            trend = tmp.groupby("month")["amount"].sum().sort_index()
            st.line_chart(trend, height=240)

        with right:
            st.subheader("Top vendors (all time)")
            vend = (
                DF.groupby("vendor", dropna=False)["amount"]
                .sum()
                .sort_values(ascending=False)
                .reset_index()
            )
            vend = vend[vend["vendor"].astype(str).str.strip() != ""].head(12)
            st.dataframe(vend, use_container_width=True, hide_index=True)

            st.subheader("Quick actions")
            qa1, qa2 = st.columns(2)
            with qa1:
                if st.button("Jump to Needs review", type="primary"):
                    st.session_state["active_tab"] = "review"
                    st.rerun()
            with qa2:
                if st.button("Build Accountant Pack"):
                    csv_bytes, zip_bytes = build_accountant_pack(WS_DIR)
                    st.download_button("Download CSV", data=csv_bytes, file_name="bookiq_export.csv", mime="text/csv")
                    st.download_button("Download Receipts ZIP", data=zip_bytes, file_name="receipts.zip", mime="application/zip")

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

        # OCR caching per file
        key = f"ocr::{up.name}::{len(file_bytes)}"
        if st.session_state.get("ocr_key") != key:
            preview_img, raw_text = ocr_upload(up.name, file_bytes)
            st.session_state["ocr_key"] = key
            st.session_state["preview_img"] = preview_img
            st.session_state["raw_text"] = raw_text

        preview_img = st.session_state.get("preview_img")
        raw_text = st.session_state.get("raw_text") or ""

        fields = extract_fields(raw_text) if raw_text else {"vendor": "", "date": "", "amount": 0.0}

        vendor0 = (fields.get("vendor") or "").strip()
        date0 = (fields.get("date") or "").strip()
        amount0 = _safe_float(fields.get("amount"), 0.0)

        suggestion = categorize(raw_text, vendor=vendor0, memory=MEM) if raw_text else {"category": "Other", "confidence": 0.0, "reasons": []}
        category0 = suggestion.get("category", "Other")
        confidence0 = _safe_float(suggestion.get("confidence"), 0.35)
        reasons0 = suggestion.get("reasons", [])

        account_code0, account_name0 = coa_for_category(category0)

        colA, colB = st.columns([1, 1], gap="large")

        with colA:
            st.subheader("Preview")
            if preview_img is not None:
                st.image(preview_img, use_container_width=True)
            with st.expander("OCR text (debug)"):
                st.code(raw_text if raw_text else "(No OCR text extracted)")
            with st.expander("Parse diagnostics"):
                st.write(
                    {
                        "vendor_confidence": fields.get("vendor_confidence"),
                        "date_confidence": fields.get("date_confidence"),
                        "amount_confidence": fields.get("amount_confidence"),
                        "parse_confidence": fields.get("parse_confidence"),
                        "vendor_candidates": fields.get("vendor_candidates"),
                    }
                )

        with colB:
            st.subheader("Extracted fields (editable)")
            vendor = st.text_input("Vendor", value=vendor0)
            date = st.text_input("Date (YYYY-MM-DD)", value=date0)
            amount = st.number_input("Total amount", min_value=0.0, value=float(amount0), step=0.01)

            # Duplicate warning
            dup = _duplicate_hint(DF, vendor, date, amount)
            if not dup.empty:
                st.warning("⚠️ Possible duplicate detected (same vendor/date/amount).")
                st.dataframe(dup, use_container_width=True, hide_index=True)

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
                index=list(COA.keys()).index(category0) if category0 in COA else list(COA.keys()).index("Other"),
            )
            account_code, account_name = coa_for_category(category)
            st.caption(f"Account: **{account_code} — {account_name}**")

            st.metric("AI confidence", f"{int(confidence0 * 100)}%")
            if reasons0:
                st.caption("Why:")
                st.write("• " + "\n• ".join(reasons0))

            needs_review_flag = _needs_review(vendor, date, amount, confidence0)
            if needs_review_flag:
                st.info("This will be marked **Needs review** until required fields are filled and/or confidence improves.")

            if st.button("Save receipt", type="primary"):
                add_txn(
                    WS_DIR,
                    date=date,
                    vendor=vendor,
                    amount=float(amount),
                    category=category,
                    account_code=account_code,
                    confidence=confidence0,
                    confidence_notes="; ".join(reasons0) if isinstance(reasons0, list) else str(reasons0),
                    job=job,
                    notes=notes,
                    receipt_bytes=file_bytes,
                    receipt_filename=up.name,
                )

                # Best-effort: immediately set needs_review correctly if storage defaults are weird
                # (Some storage layers compute it; this ensures correctness if not.)
                # We need the latest rows to find the newest id — if storage assigns id.
                try:
                    latest = list_txns(WS_DIR, include_deleted=False)
                    if latest:
                        last = latest[-1]
                        update_txn(
                            WS_DIR,
                            last["id"],
                            {
                                "needs_review": needs_review_flag,
                                "updated_at": _utc_now(),
                            },
                        )
                except Exception:
                    pass

                if job:
                    remember_job(MEM, job)
                remember_vendor_mapping(MEM, vendor=vendor, category=category, account_code=account_code)
                save_memory(WS_DIR, MEM)

                st.success("Saved ✅")
                st.rerun()

# ==========================================================
# 2) NEEDS REVIEW
# ==========================================================
with tab_review:
    st.header("Needs review")

    rows = list_txns(WS_DIR, include_deleted=False)
    df = _make_df(rows)
    review = df[df["needs_review"] == 1].copy()

    if review.empty:
        st.success("Nothing needs review 🎉")
    else:
        st.write("These receipts need a quick check (missing fields or low confidence).")

        # Bulk tools (the “umph”)
        st.subheader("Bulk actions")
        ids = review["id"].astype(str).tolist()
        bulk_ids = st.multiselect("Select receipt IDs", options=ids, default=[])
        b1, b2, b3 = st.columns([1, 1, 1])
        with b1:
            if st.button("Bulk approve selected", type="primary") and bulk_ids:
                for _id in bulk_ids:
                    update_txn(WS_DIR, _id, {"needs_review": 0, "approved_at": _utc_now(), "updated_at": _utc_now()})
                st.success(f"Approved {len(bulk_ids)} ✅")
                st.rerun()
        with b2:
            if st.button("Bulk delete selected") and bulk_ids:
                for _id in bulk_ids:
                    soft_delete_txn(WS_DIR, _id)
                st.warning(f"Moved {len(bulk_ids)} to Recently deleted 🗑️")
                st.rerun()
        with b3:
            if st.button("Recompute needs_review for ALL"):
                for r in rows:
                    nr = _needs_review(r.get("vendor", ""), r.get("date", ""), _safe_float(r.get("amount"), 0.0), _safe_float(r.get("confidence"), 0.0))
                    update_txn(WS_DIR, r["id"], {"needs_review": nr, "updated_at": _utc_now()})
                st.success("Recomputed ✅")
                st.rerun()

        st.divider()

        # Individual review cards
        for r in review.head(200).to_dict(orient="records"):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])

                with c1:
                    st.markdown(f"**{r.get('vendor','(missing vendor)') or '(missing vendor)'}**")
                    st.caption(f"ID: {r['id']} • Confidence: {float(r.get('confidence') or 0):.2f}")
                    st.write(f"Date: `{r.get('date','')}`  |  Amount: **${float(r.get('amount') or 0):.2f}**")
                    st.write(f"Category: `{r.get('category','Other')}`  |  Account: `{r.get('account_code','')}`")

                    # Receipt preview
                    receipt_path = (r.get("receipt_path") or "").strip()
                    p = WS_DIR / receipt_path if receipt_path else None
                    if p and p.exists():
                        with st.expander("Show receipt image"):
                            st.image(str(p), use_container_width=True)

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
                        else list(COA.keys()).index("Other"),
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
                        new_needs_review = _needs_review(new_vendor, new_date, float(new_amount), max(float(r.get("confidence") or 0), 0.90))
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
                                "needs_review": new_needs_review,
                                "updated_at": _utc_now(),
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
    df = _make_df(rows)

    if df.empty:
        st.info("No receipts yet.")
    else:
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

        # Bulk edit (umph)
        st.subheader("Bulk edit")
        bulk_ids = st.multiselect("Select IDs from the table to bulk edit", options=view["id"].astype(str).tolist(), default=[])
        bcol1, bcol2, bcol3, bcol4 = st.columns([1, 1, 1, 1])
        with bcol1:
            bulk_cat = st.selectbox("Set category", options=["(no change)"] + list(COA.keys()))
        with bcol2:
            bulk_job = st.text_input("Set job", value="", placeholder="(leave blank for no change)")
        with bcol3:
            bulk_notes_append = st.text_input("Append note", value="", placeholder="(optional)")
        with bcol4:
            if st.button("Apply bulk changes", type="primary") and bulk_ids:
                for _id in bulk_ids:
                    patch = {"updated_at": _utc_now()}
                    if bulk_cat != "(no change)":
                        code, _ = coa_for_category(bulk_cat)
                        patch["category"] = bulk_cat
                        patch["account_code"] = code
                    if (bulk_job or "").strip():
                        patch["job"] = bulk_job.strip()
                        remember_job(MEM, bulk_job.strip())
                    if (bulk_notes_append or "").strip():
                        # simple append
                        old = df[df["id"].astype(str) == str(_id)]["notes"].iloc[0] if not df[df["id"].astype(str) == str(_id)].empty else ""
                        patch["notes"] = (str(old) + " " + bulk_notes_append.strip()).strip()
                    update_txn(WS_DIR, _id, patch)
                save_memory(WS_DIR, MEM)
                st.success(f"Updated {len(bulk_ids)} receipts ✅")
                st.rerun()

        st.divider()

        left, right = st.columns([1.2, 0.8], gap="large")

        with left:
            st.dataframe(view[show_cols], use_container_width=True, hide_index=True)
            selected_id = st.text_input(
                "Open receipt by ID (copy from table)",
                value=st.session_state.get("selected_id", "")
            )
            st.session_state["selected_id"] = selected_id.strip()

        with right:
            st.subheader("Receipt details")
            sel = st.session_state.get("selected_id", "").strip()
            if not sel:
                st.info("Paste an ID to view/edit.")
            else:
                rec_map = {str(r["id"]): r for r in rows}
                r = rec_map.get(str(sel))
                if not r:
                    st.warning("ID not found (or deleted).")
                else:
                    receipt_path = (r.get("receipt_path") or "").strip()
                    p = WS_DIR / receipt_path if receipt_path else None
                    if p and p.exists():
                        st.image(str(p), use_container_width=True)
                    else:
                        st.caption("Receipt image not found.")

                    ev = st.text_input("Vendor", value=r.get("vendor", ""), key=f"ev_{sel}")
                    ed = st.text_input("Date", value=r.get("date", ""), key=f"ed_{sel}")
                    ea = st.number_input("Amount", min_value=0.0, value=float(r.get("amount") or 0), step=0.01, key=f"ea_{sel}")

                    ec = st.selectbox(
                        "Category",
                        options=list(COA.keys()),
                        index=list(COA.keys()).index(r.get("category", "Other")) if r.get("category", "Other") in COA else list(COA.keys()).index("Other"),
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
                            # keep original confidence unless you change your pipeline later
                            conf = _safe_float(r.get("confidence"), 0.0)
                            nr = _needs_review(ev, ed, float(ea), conf)

                            update_txn(WS_DIR, sel, {
                                "vendor": ev,
                                "date": ed,
                                "amount": float(ea),
                                "category": ec,
                                "account_code": code,
                                "job": ej,
                                "notes": en,
                                "updated_at": _utc_now(),
                                "needs_review": nr,
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
    df = _make_df(rows)

    if df.empty:
        st.info("No receipts yet.")
    else:
        df["month"] = df["date"].astype(str).str.slice(0, 7)
        pnl = df.pivot_table(index="month", columns="category", values="amount", aggfunc="sum", fill_value=0).sort_index()

        st.subheader("Monthly expense summary (P&L-style)")
        st.dataframe(pnl, use_container_width=True)

        # “umph”: toggle between total line + category stack table
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
        ddf = _make_df(deleted)

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
"""
    )




