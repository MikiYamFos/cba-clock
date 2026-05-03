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
                        st.error(
                            f"Upload failed: {response.text}"
                        )
                except requests.exceptions.ConnectionError:
                    st.error(
                        "Cannot connect to backend. Is it running? Run `make up` or `make dev`."
                    )
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
                        st.info(
                            "Processing takes a few minutes. Check Airflow at http://localhost:8080 for status."
                        )
                    else:
                        st.error(
                            f"Upload failed: {response.text}"
                        )
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
                    data = response.json()
                    st.success(
                        f"✅ Reprocess triggered. DAG run ID: `{data['dag_run_id']}`"
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
                # Status filter
                status_filter = st.multiselect(
                    "Filter by status",
                    options=["indexed", "metadata_only", "unprocessed"],
                    default=["indexed", "metadata_only", "unprocessed"],
                )

                filtered = [c for c in contracts if c["status"] in status_filter]
                st.caption(f"Showing {len(filtered)} of {len(contracts)} contract(s)")

                # Select all / none
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

                # Contract list with checkboxes
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

                    # Show metadata inline
                    if (
                        c.get("union_name")
                        or c.get("location")
                        or c.get("last_processed_at")
                    ):
                        details = []
                        if c.get("union_name"):
                            details.append(
                                f"Union: {c['union_name']} {c.get('union_local') or ''}"
                            )
                        if c.get("location"):
                            details.append(f"Location: {c['location']}")
                        if c.get("last_processed_at"):
                            details.append(
                                f"Last processed: {c['last_processed_at'][:10]}"
                            )
                        st.caption(" · ".join(details))

                st.divider()

                # Process / reprocess selected
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
                                        f"✅ Pipeline triggered for {len(selected)} file(s). DAG run ID: `{data['dag_run_id']}`"
                                    )
                                    st.info(
                                        "Check Airflow at http://localhost:8080 for status."
                                    )
                                else:
                                    st.error(
                                        f"Failed: {response.text}"
                                    )
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
        "Search for a contract to see Claude's extracted timing rules alongside the exact contract text. Verify the wording is correct and correct any mistakes."
    )

    contract_query = st.text_input(
        "Search for a contract", placeholder="e.g. Verizon, HUD, Hilton"
    )

    if contract_query:
        try:
            response = requests.get(
                f"{BACKEND_URL}/search",
                params={"q": contract_query, "size": 20},
                timeout=10,
            )
            if response.status_code == 200:
                results = response.json()
                hits = results.get("hits", [])

                if not hits:
                    st.warning("No contracts found matching that search.")
                else:
                    contracts = list({h["source_file"]: h for h in hits}.keys())
                    selected_contract = st.selectbox("Select contract", contracts)

                    if selected_contract:
                        section_response = requests.get(
                            f"{BACKEND_URL}/search/{selected_contract}",
                            params={"q": contract_query, "size": 20},
                            timeout=10,
                        )
                        if section_response.status_code == 200:
                            section_results = section_response.json()
                            section_hits = section_results.get("hits", [])

                            for hit in section_hits:
                                with st.expander(
                                    f"Article {hit['article_number']} — {hit['article_title']}"
                                ):
                                    if hit.get("labels"):
                                        st.caption(" · ".join(hit["labels"]))

                                    st.markdown("**Matching contract language:**")
                                    for fragment in hit.get("highlights", []):
                                        highlighted = fragment.replace(
                                            "<em>", "**"
                                        ).replace("</em>", "**")
                                        st.markdown(f"> {highlighted}")

                                    st.divider()
                                    st.markdown("**Extracted timing rules:**")
                                    st.caption(
                                        "Timing rule corrections will be available once Postgres integration is complete."
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
