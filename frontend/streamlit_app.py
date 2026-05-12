import os
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = f"http://localhost:{os.environ.get('BACKEND_PORT', '8000')}"

st.set_page_config(page_title="CBA Clock", layout="wide")
st.title("CBA Clock")

upload_tab, contracts_tab, review_tab, search_tab = st.tabs(
    ["Upload", "Contracts", "Review", "Search"]
)

# ---------------------------------------------------------------------------
# Upload Tab
# ---------------------------------------------------------------------------

with upload_tab:
    st.subheader("Upload Contracts")
    st.write(
        "Upload one or more CBA PDFs. Each file will be processed automatically — text extracted, sections indexed, and timing rules pulled out by Claude."
    )

    uploaded_files = st.file_uploader(
        "Choose PDF(s)",
        type=["pdf"],
        accept_multiple_files=True,
    )

    overwrite = st.checkbox(
        "Replace existing contract if already uploaded",
        value=False,
        help="Use this if you have a better copy of a contract that's already in the system.",
    )

    if uploaded_files and st.button("Upload and Process"):
        if len(uploaded_files) == 1:
            file = uploaded_files[0]
            with st.spinner(f"Uploading {file.name}..."):
                try:
                    response = requests.post(
                        f"{BACKEND_URL}/upload",
                        files={"file": (file.name, file.getvalue(), "application/pdf")},
                        params={"overwrite": overwrite},
                        timeout=30,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        st.success(
                            f"✅ {file.name} uploaded. Pipeline triggered — DAG run ID: `{data['dag_run_id']}`"
                        )
                        st.info(
                            "Processing takes a few minutes. Check Airflow at http://localhost:8080 for status."
                        )
                    elif response.status_code == 409:
                        st.warning(
                            f"⚠️ {file.name} already exists. Enable 'Replace existing' to overwrite."
                        )
                    else:
                        st.error(f"Upload failed: {response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to backend. Is it running?")
        else:
            with st.spinner(f"Uploading {len(uploaded_files)} files..."):
                try:
                    files = [
                        ("files", (f.name, f.getvalue(), "application/pdf"))
                        for f in uploaded_files
                    ]
                    response = requests.post(
                        f"{BACKEND_URL}/upload/bulk",
                        files=files,
                        params={"overwrite": overwrite},
                        timeout=30,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        st.success(
                            f"✅ {len(data['saved'])} file(s) uploaded. Pipeline triggered — DAG run ID: `{data['dag_run_id']}`"
                        )
                        if data["skipped"]:
                            st.warning(
                                f"Skipped: {[s['filename'] for s in data['skipped']]}"
                            )
                    else:
                        st.error(f"Upload failed: {response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to backend.")

    st.divider()
    st.subheader("Reprocess an Existing Contract")
    reprocess_filename = st.text_input(
        "Filename (e.g. dol_5_VERIZON_DELAWARE_INC_VERIZON_SERVICES_CORP.pdf)"
    )
    if reprocess_filename and st.button("Reprocess"):
        with st.spinner(f"Triggering reprocess for {reprocess_filename}..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/reprocess/{reprocess_filename}", timeout=30
                )
                if response.status_code == 200:
                    st.success(
                        f"✅ Reprocess triggered. DAG run ID: `{response.json()['dag_run_id']}`"
                    )
                elif response.status_code == 404:
                    st.error(f"{reprocess_filename} not found.")
                else:
                    st.error(f"Failed: {response.text}")
            except requests.exceptions.ConnectionError:
                st.error("Cannot connect to backend.")

# ---------------------------------------------------------------------------
# Contracts Tab
# ---------------------------------------------------------------------------

with contracts_tab:
    st.subheader("All Contracts")
    st.write(
        "View every contract on disk, its processing status, and trigger processing for any selection."
    )

    try:
        response = requests.get(f"{BACKEND_URL}/contracts/available", timeout=10)
        if response.status_code != 200:
            st.error(f"Failed to load contracts: {response.text}")
        else:
            contracts = response.json()
            if not contracts:
                st.info("No PDFs found in raw_pdfs directory.")
            else:
                status_filter = st.multiselect(
                    "Filter by status",
                    options=["indexed", "metadata_only", "unprocessed"],
                    default=["indexed", "metadata_only", "unprocessed"],
                )
                filtered = [c for c in contracts if c["status"] in status_filter]
                st.caption(f"Showing {len(filtered)} of {len(contracts)} contract(s)")

                col1, col2, col3 = st.columns([1, 1, 4])
                select_all = col1.button("Select All")
                select_none = col2.button("Select None")

                if "selected_contracts" not in st.session_state:
                    st.session_state.selected_contracts = set()

                if select_all:
                    st.session_state.selected_contracts = {
                        c["filename"] for c in filtered
                    }
                if select_none:
                    st.session_state.selected_contracts = set()

                for c in filtered:
                    status_icon = {
                        "indexed": "✅",
                        "metadata_only": "⚠️",
                        "unprocessed": "⬜",
                    }.get(c["status"], "")
                    label = c.get("employer_name") or c["filename"]
                    version = c.get("version_label") or ""
                    expiration = c.get("expiration_date") or ""

                    checked = st.checkbox(
                        f"{status_icon} **{label}** {version} {expiration}",
                        value=c["filename"] in st.session_state.selected_contracts,
                        key=f"cb_{c['filename']}",
                    )
                    if checked:
                        st.session_state.selected_contracts.add(c["filename"])
                    else:
                        st.session_state.selected_contracts.discard(c["filename"])

                    details = []
                    if c.get("union_name"):
                        details.append(
                            f"Union: {c['union_name']} {c.get('union_local') or ''}"
                        )
                    if c.get("location"):
                        details.append(f"Location: {c['location']}")
                    if c.get("last_processed_at"):
                        details.append(f"Last processed: {c['last_processed_at'][:10]}")
                    if details:
                        st.caption(" · ".join(details))

                st.divider()
                selected = st.session_state.selected_contracts
                if selected:
                    st.caption(f"{len(selected)} contract(s) selected")
                    overwrite_sel = st.checkbox(
                        "Reprocess even if already indexed", key="overwrite_selection"
                    )
                    if st.button(f"Process {len(selected)} selected contract(s)"):
                        with st.spinner("Triggering pipeline..."):
                            try:
                                response = requests.post(
                                    f"{BACKEND_URL}/contracts/process",
                                    json=list(selected),
                                    params={"overwrite": overwrite_sel},
                                    timeout=30,
                                )
                                if response.status_code == 200:
                                    data = response.json()
                                    st.success(
                                        f"✅ Pipeline triggered. DAG run ID: `{data['dag_run_id']}`"
                                    )
                                    st.info(
                                        "Check Airflow at http://localhost:8080 for status."
                                    )
                                else:
                                    st.error(f"Failed: {response.text}")
                            except requests.exceptions.ConnectionError:
                                st.error("Cannot connect to backend.")
                else:
                    st.caption("No contracts selected.")

    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend. Is it running?")

# ---------------------------------------------------------------------------
# Review Tab
# ---------------------------------------------------------------------------

with review_tab:
    st.subheader("Review Extracted Rules")
    st.write(
        "Select a contract to review Claude's extracted timing rules. "
        "For each rule, verify the quote matches the contract text on the left, "
        "then confirm or correct the extracted fields on the right."
    )

    # Contract selector
    try:
        contracts_response = requests.get(f"{BACKEND_URL}/contracts", timeout=10)
        if contracts_response.status_code == 200:
            all_cbas = contracts_response.json()
            if not all_cbas:
                st.info("No contracts with timing rules yet. Run the pipeline first.")
            else:
                contract_options = {
                    f"{c.get('employer_name') or c['source_file']} ({c.get('version_label') or 'unknown version'})": c[
                        "source_file"
                    ]
                    for c in all_cbas
                }
                selected_label = st.selectbox(
                    "Select contract to review", options=list(contract_options.keys())
                )
                selected_source_file = contract_options[selected_label]

                # Load timing rules for this contract
                rules_response = requests.get(
                    f"{BACKEND_URL}/timing-rules/{selected_source_file}", timeout=10
                )
                if rules_response.status_code != 200:
                    st.error(f"Failed to load timing rules: {rules_response.text}")
                else:
                    rules = rules_response.json()
                    if not rules:
                        st.info("No timing rules extracted for this contract yet.")
                    else:
                        # Group rules by article
                        articles = {}
                        for rule in rules:
                            key = rule["article_number"]
                            if key not in articles:
                                articles[key] = {
                                    "title": rule["article_title"],
                                    "rules": [],
                                }
                            articles[key]["rules"].append(rule)

                        st.caption(
                            f"{len(rules)} timing rules across {len(articles)} articles"
                        )

                        # Article filter
                        article_options = [
                            f"Article {num} — {data['title']}"
                            for num, data in sorted(
                                articles.items(), key=lambda x: x[0]
                            )
                        ]
                        selected_article_label = st.selectbox(
                            "Jump to article", options=article_options
                        )
                        selected_article_num = (
                            selected_article_label.split(" — ")[0]
                            .replace("Article ", "")
                            .strip()
                        )

                        st.divider()

                        # Show rules for selected article
                        article_data = articles.get(selected_article_num)
                        if article_data:
                            st.markdown(
                                f"### Article {selected_article_num} — {article_data['title']}"
                            )



                            for rule in article_data["rules"]:
                                # Load section context once for this article
                                context_response = requests.get(
                                    f"{BACKEND_URL}/sections/{selected_source_file}/{selected_article_num}/context",
                                    params={"quote": rule.get("effective_quote", "")},
                                    timeout=10,
                                )

                                section_text = ""
                                if context_response.status_code == 200:
                                    ctx = context_response.json()
                                    section_text = ctx.get("section_text", "")
                                
                                has_correction = rule.get("correction_id") is not None
                                rule_id = rule["rule_id"]
                                correction_label = (
                                    " ✏️ corrected" if has_correction else ""
                                )

                                with st.expander(
                                    f"Rule {rule_id}{correction_label} — {rule['effective_action']}",
                                    expanded=True,
                                ):

                                    # Two column layout — contract text left, extracted rule right
                                    left_col, right_col = st.columns(
                                        [1, 1], gap="large"
                                    )

                                    with left_col:
                                        st.markdown("**Contract text**")
                                        quote = rule.get("effective_quote", "")

                                        if section_text and quote:
                                            # Find quote position and highlight it
                                            idx = section_text.lower().find(
                                                quote.lower()[:50]
                                            )
                                            if idx != -1:
                                                # Show context window around the quote (300 chars before/after)
                                                window_start = max(0, idx - 300)
                                                window_end = min(
                                                    len(section_text),
                                                    idx + len(quote) + 300,
                                                )
                                                before = section_text[window_start:idx]
                                                matched = section_text[
                                                    idx : idx + len(quote)
                                                ]
                                                after = section_text[
                                                    idx + len(quote) : window_end
                                                ]

                                                # Render with highlight
                                                st.markdown(
                                                    f'<div style="font-family: monospace; font-size: 0.85em; '
                                                    f"line-height: 1.6; background: #f8f9fa; padding: 12px; "
                                                    f'border-radius: 6px; border-left: 3px solid #dee2e6;">'
                                                    f"{before}"
                                                    f'<mark style="background: #fff3cd; padding: 2px 0;">{matched}</mark>'
                                                    f"{after}"
                                                    f"</div>",
                                                    unsafe_allow_html=True,
                                                )
                                            else:
                                                st.markdown(
                                                    f'<div style="font-family: monospace; font-size: 0.85em; '
                                                    f"line-height: 1.6; background: #f8f9fa; padding: 12px; "
                                                    f'border-radius: 6px;">'
                                                    f"{section_text[:600]}..."
                                                    f"</div>",
                                                    unsafe_allow_html=True,
                                                )
                                        else:
                                            st.caption("Contract text not available")

                                    with right_col:
                                        st.markdown("**Extracted rule**")

                                        # Show original vs corrected clearly
                                        unit_display = rule["effective_unit"].replace(
                                            "_", " "
                                        )
                                        st.markdown(
                                            f"**Action:** {rule['effective_action']}  \n"
                                            f"**Trigger:** {rule['effective_trigger']}  \n"
                                            f"**Deadline:** {rule['effective_offset_days']} {unit_display}  \n"
                                            f"**Party:** {rule.get('effective_party') or '—'}"
                                        )

                                        if rule.get("is_ambiguous"):
                                            st.warning(
                                                f"⚠️ Ambiguous: {rule.get('ambiguity_note')}"
                                            )

                                        if has_correction:
                                            st.caption(
                                                f"✏️ Corrected by {rule.get('corrected_by') or 'unknown'} "
                                                f"on {str(rule.get('corrected_at', ''))[:10]}"
                                            )
                                            if rule.get("correction_note"):
                                                st.caption(
                                                    f"Note: {rule['correction_note']}"
                                                )

                                        st.divider()

                                        # Correction form — collapsed by default
                                        correction_key = (
                                            f"show_correction_{rule['timing_rule_id']}"
                                        )
                                        if correction_key not in st.session_state:
                                            st.session_state[correction_key] = False

                                        if st.button(
                                            (
                                                "✏️ Correct this rule"
                                                if not has_correction
                                                else "✏️ Update correction"
                                            ),
                                            key=f"btn_correct_{rule['timing_rule_id']}",
                                        ):
                                            st.session_state[correction_key] = (
                                                not st.session_state[correction_key]
                                            )

                                        if st.session_state[correction_key]:
                                            st.markdown(
                                                "**Make a correction** — only fill in what's wrong:"
                                            )

                                            c_action = st.text_input(
                                                "Action",
                                                value=rule.get("corrected_action")
                                                or "",
                                                placeholder=rule["original_action"],
                                                key=f"c_action_{rule['timing_rule_id']}",
                                            )
                                            c_trigger = st.text_input(
                                                "Trigger",
                                                value=rule.get("corrected_trigger")
                                                or "",
                                                placeholder=rule["original_trigger"],
                                                key=f"c_trigger_{rule['timing_rule_id']}",
                                            )
                                            c_offset = st.number_input(
                                                "Offset days",
                                                value=rule.get("corrected_offset_days")
                                                or rule["original_offset_days"],
                                                min_value=0,
                                                key=f"c_offset_{rule['timing_rule_id']}",
                                            )
                                            c_unit = st.selectbox(
                                                "Unit",
                                                options=[
                                                    "calendar_days",
                                                    "working_days",
                                                    "business_days",
                                                    "weeks",
                                                    "months",
                                                    "hours",
                                                ],
                                                index=[
                                                    "calendar_days",
                                                    "working_days",
                                                    "business_days",
                                                    "weeks",
                                                    "months",
                                                    "hours",
                                                ].index(
                                                    rule.get("corrected_unit")
                                                    or rule["original_unit"]
                                                ),
                                                key=f"c_unit_{rule['timing_rule_id']}",
                                            )
                                            c_party = st.text_input(
                                                "Party",
                                                value=rule.get("corrected_party")
                                                or rule.get("original_party")
                                                or "",
                                                key=f"c_party_{rule['timing_rule_id']}",
                                            )
                                            c_quote = st.text_area(
                                                "Correct quote from contract",
                                                value=rule.get("corrected_quote")
                                                or rule.get("original_quote")
                                                or "",
                                                key=f"c_quote_{rule['timing_rule_id']}",
                                            )
                                            c_note = st.text_input(
                                                "Why are you correcting this? (optional)",
                                                key=f"c_note_{rule['timing_rule_id']}",
                                            )
                                            c_by = st.text_input(
                                                "Your name (optional)",
                                                key=f"c_by_{rule['timing_rule_id']}",
                                            )

                                            if st.button(
                                                "Save correction",
                                                key=f"save_{rule['timing_rule_id']}",
                                            ):
                                                try:
                                                    payload = {
                                                        "action": c_action or None,
                                                        "trigger": c_trigger or None,
                                                        "offset_days": int(c_offset),
                                                        "unit": c_unit,
                                                        "party": c_party or None,
                                                        "quote": c_quote or None,
                                                        "correction_note": c_note
                                                        or None,
                                                        "corrected_by": c_by or None,
                                                    }
                                                    save_response = requests.post(
                                                        f"{BACKEND_URL}/timing-rules/{rule['timing_rule_id']}/correction",
                                                        json=payload,
                                                        timeout=10,
                                                    )
                                                    if save_response.status_code == 200:
                                                        st.success(
                                                            "✅ Correction saved."
                                                        )
                                                        st.session_state[
                                                            correction_key
                                                        ] = False
                                                        st.rerun()
                                                    else:
                                                        st.error(
                                                            f"Failed to save: {save_response.text}"
                                                        )
                                                except (
                                                    requests.exceptions.ConnectionError
                                                ):
                                                    st.error(
                                                        "Cannot connect to backend."
                                                    )

    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend. Is it running?")

# ---------------------------------------------------------------------------
# Search Tab
# ---------------------------------------------------------------------------

with search_tab:
    st.subheader("Search Contracts")

    col1, col2 = st.columns([3, 1])
    with col1:
        search_query = st.text_input(
            "Search", placeholder="e.g. grievance within 10 working days"
        )
    with col2:
        phrase_match = st.checkbox("Exact phrase", value=False)
        contract_filter = st.text_input(
            "Limit to contract (optional)", placeholder="filename.txt"
        )

    if search_query and st.button("Search"):
        try:
            if contract_filter:
                response = requests.get(
                    f"{BACKEND_URL}/search/{contract_filter}",
                    params={"q": search_query, "phrase": phrase_match, "size": 20},
                    timeout=10,
                )
            else:
                response = requests.get(
                    f"{BACKEND_URL}/search",
                    params={"q": search_query, "phrase": phrase_match, "size": 20},
                    timeout=10,
                )

            if response.status_code == 200:
                results = response.json()
                total = results.get("total_hits", 0)
                hits = results.get("hits", [])
                st.caption(f"{total} result(s) found")

                for hit in hits:
                    with st.expander(
                        f"{hit['source_file']} — Article {hit['article_number']}: {hit['article_title']}"
                    ):
                        if hit.get("labels"):
                            st.caption(" · ".join(hit["labels"]))
                        for fragment in hit.get("highlights", []):
                            highlighted = fragment.replace("<em>", "**").replace(
                                "</em>", "**"
                            )
                            st.markdown(f"> {highlighted}")
            elif response.status_code == 422:
                st.error("Search query too short.")
            else:
                st.error(f"Search failed: {response.text}")

        except requests.exceptions.ConnectionError:
            st.error("Cannot connect to backend. Is it running?")
