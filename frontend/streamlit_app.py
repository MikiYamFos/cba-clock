import json
from pathlib import Path

import streamlit as st


st.set_page_config(page_title="CBA Clock", layout="wide")

st.title("CBA Clock")
st.write("Review extracted contract timeline rules before generating deadlines and calendar outputs.")

uploaded_file = st.file_uploader("Upload a CBA PDF", type=["pdf"])

if uploaded_file:
    st.success(f"Uploaded: {uploaded_file.name}")

st.subheader("Extracted Rules")

sample_rules = [
    {
        "clause_type": "grievance_filing_window",
        "trigger": "occurrence",
        "offset": 10,
        "unit": "working_days",
        "requires_user_input": True,
    }
]

edited = st.data_editor(sample_rules, num_rows="dynamic")

st.subheader("JSON Output")
st.json(edited)