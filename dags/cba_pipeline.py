from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/cba_clock")

from datetime import datetime, timedelta

import pandas as pd
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator

from worker.app.sample_sources.opm_downloader import OPMDownloader
from worker.app.pdf_text import PDFTextExtractor
from worker.app.section_extraction import ContractSection, ContractSectionExtractor
from worker.app.claude_extraction import DeadlineExtractor
from worker.app.es_client import CBASectionIndexer
from worker.app.metadata_lookup import lookup
from worker.app.db import upsert_cba

PROJECT_ROOT = Path("/opt/cba_clock")
RAW_PDF_DIR = PROJECT_ROOT / "data" / "samples" / "raw_pdfs"
TEXT_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "extracted_text"
REPORT_OUTPUT = PROJECT_ROOT / "data" / "processed" / "quality_report.csv"
INVENTORY_OUTPUT = PROJECT_ROOT / "data" / "processed" / "section_inventory.csv"
DEADLINES_OUTPUT = PROJECT_ROOT / "data" / "processed" / "deadline_extractions.jsonl"

default_args = {
    "owner": "cba-clock",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}

# ---------------------------------------------------------------------------
# Helper: resolve which PDFs to process from dag_run.conf
#
# dag_run.conf options:
#   filename:  "foo.pdf"              → process one file
#   filenames: ["foo.pdf","bar.pdf"]  → process a specific set
#   (neither)                         → process all PDFs in RAW_PDF_DIR
#   overwrite: true                   → re-process even if already done
# ---------------------------------------------------------------------------

def get_target_pdfs(context) -> list[Path]:
    conf = context["dag_run"].conf or {}
    if "filename" in conf:
        return [RAW_PDF_DIR / conf["filename"]]
    if "filenames" in conf:
        return [RAW_PDF_DIR / f for f in conf["filenames"]]
    return list(RAW_PDF_DIR.glob("*.pdf"))


def task_download_pdfs(**context) -> None:
    conf = context["dag_run"].conf or {}
    if "filename" in conf or "filenames" in conf:
        print("Single/selective file mode — skipping OPM download")
        return
    overwrite = conf.get("overwrite", False)
    downloader = OPMDownloader(project_root=PROJECT_ROOT)
    manifest = downloader.download(limit=None, overwrite=overwrite)
    print(f"Download complete: {len(manifest)} records")


def task_extract_text(**context) -> None:
    conf = context["dag_run"].conf or {}
    overwrite = conf.get("overwrite", False)
    extractor = PDFTextExtractor()
    pdfs = get_target_pdfs(context)
    print(f"Extracting text from {len(pdfs)} PDF(s)")

    for pdf_path in pdfs:
        if not pdf_path.exists():
            print(f"  WARNING: {pdf_path.name} not found in {RAW_PDF_DIR}")
            continue
        output_file = TEXT_OUTPUT_DIR / f"{pdf_path.stem}.txt"
        if output_file.exists() and not overwrite:
            print(f"  Skipping {pdf_path.name} (already extracted)")
            continue
        result = extractor.extract(pdf_path)
        extractor.save_text(result, TEXT_OUTPUT_DIR)
        print(f"  {pdf_path.name}: {result.extraction_quality} ({result.total_chars} chars)")


def task_populate_cba_metadata(**context) -> None:
    pdfs = get_target_pdfs(context)
    print(f"Populating CBA metadata for {len(pdfs)} file(s)")

    for pdf_path in pdfs:
        metadata = lookup(pdf_path.name)
        if not metadata:
            print(f"  WARNING: No metadata found for {pdf_path.name} in either source CSV")
            continue
        cba_id = upsert_cba(metadata)
        print(f"  {pdf_path.name}: cba.id={cba_id} employer={metadata.employer_name} expiration={metadata.expiration_date}")


def task_quality_report(**context) -> None:
    pdfs = get_target_pdfs(context)
    extractor = PDFTextExtractor()
    rows = []

    if REPORT_OUTPUT.exists():
        existing = pd.read_csv(REPORT_OUTPUT)
    else:
        existing = pd.DataFrame()

    for pdf_path in pdfs:
        if not pdf_path.exists():
            continue
        result = extractor.extract(pdf_path)
        rows.append({
            "filename": pdf_path.name,
            "page_count": result.page_count,
            "pages_with_text": result.pages_with_text,
            "total_chars": result.total_chars,
            "avg_chars_per_page": round(result.avg_chars_per_page, 1),
            "quality": result.extraction_quality,
            "flags": "|".join(result.quality_flags),
        })

    new_df = pd.DataFrame(rows)

    if not existing.empty and not new_df.empty:
        updated = existing[~existing["filename"].isin(new_df["filename"])]
        combined = pd.concat([updated, new_df], ignore_index=True)
    elif not new_df.empty:
        combined = new_df
    else:
        combined = existing

    REPORT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(REPORT_OUTPUT, index=False)
    print(f"Quality report updated: {len(rows)} file(s) processed")


def task_section_inventory(**context) -> None:
    pdfs = get_target_pdfs(context)
    target_stems = {p.stem + ".txt" for p in pdfs}
    section_extractor = ContractSectionExtractor()

    if INVENTORY_OUTPUT.exists():
        existing = pd.read_csv(INVENTORY_OUTPUT)
    else:
        existing = pd.DataFrame()

    rows = []
    for text_path in [TEXT_OUTPUT_DIR / stem for stem in target_stems]:
        if not text_path.exists():
            print(f"  WARNING: {text_path.name} not found — was text extraction successful?")
            continue
        sections = section_extractor.extract_sections(text_path)
        for section in sections:
            labels = section_extractor.classify_section(section)
            rows.append({
                "source_file": section.source_file,
                "article_number": section.article_number,
                "article_title": section.article_title,
                "start_char": section.start_char,
                "end_char": section.end_char,
                "char_length": section.end_char - section.start_char,
                "labels": "|".join(labels),
            })

    new_df = pd.DataFrame(rows)

    if not existing.empty and not new_df.empty:
        updated = existing[~existing["source_file"].isin(new_df["source_file"])]
        combined = pd.concat([updated, new_df], ignore_index=True)
    elif not new_df.empty:
        combined = new_df
    else:
        combined = existing

    INVENTORY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(INVENTORY_OUTPUT, index=False)
    print(f"Section inventory updated: {len(rows)} sections from {len(target_stems)} file(s)")


def task_index_sections(**context) -> None:
    conf = context["dag_run"].conf or {}
    overwrite = conf.get("overwrite", False)
    pdfs = get_target_pdfs(context)
    target_stems = {p.stem + ".txt" for p in pdfs}

    if not INVENTORY_OUTPUT.exists():
        raise FileNotFoundError(f"Section inventory not found at {INVENTORY_OUTPUT}")

    indexer = CBASectionIndexer()
    indexer.ensure_index(recreate=False)

    if overwrite:
        for stem in target_stems:
            indexer.delete_by_source(stem)

    inventory = pd.read_csv(INVENTORY_OUTPUT)
    inventory = inventory[inventory["source_file"].isin(target_stems)]

    sections: list[ContractSection] = []
    labels_by_section: dict[tuple[str, str], list[str]] = {}

    for _, row in inventory.iterrows():
        text_path = TEXT_OUTPUT_DIR / row["source_file"]
        if not text_path.exists():
            print(f"  WARNING: Text file not found: {text_path}")
            continue

        full_text = text_path.read_text(encoding="utf-8")
        section_text = full_text[int(row["start_char"]) : int(row["end_char"])]

        section = ContractSection(
            source_file=row["source_file"],
            article_number=str(row["article_number"]),
            article_title=row["article_title"],
            start_char=int(row["start_char"]),
            end_char=int(row["end_char"]),
            text=section_text.strip(),
        )
        sections.append(section)

        labels = (
            row["labels"].split("|")
            if pd.notna(row.get("labels")) and row.get("labels")
            else []
        )
        labels_by_section[(row["source_file"], str(row["article_number"]))] = labels

    if not sections:
        print("No new sections to index.")
        return

    print(f"Indexing {len(sections)} sections from {len(target_stems)} file(s)...")
    success, errors = indexer.index_sections(
        sections=sections,
        labels_by_section=labels_by_section,
        overwrite_source=False,
    )
    print(f"Indexing complete: {success} indexed, {errors} errors")
    print(f"Total sections in index: {indexer.count()}")


def task_extract_deadlines(**context) -> None:
    conf = context["dag_run"].conf or {}
    overwrite = conf.get("overwrite", False)
    pdfs = get_target_pdfs(context)
    target_stems = {p.stem + ".txt" for p in pdfs}

    extractor = DeadlineExtractor()

    if overwrite:
        if DEADLINES_OUTPUT.exists():
            kept = []
            with DEADLINES_OUTPUT.open() as f:
                for line in f:
                    record = json.loads(line)
                    if record["source_file"] not in target_stems:
                        kept.append(line)
            with DEADLINES_OUTPUT.open("w") as f:
                f.writelines(kept)
            print(f"Removed existing extractions for {len(target_stems)} file(s)")

    if not INVENTORY_OUTPUT.exists():
        raise FileNotFoundError(f"Section inventory not found at {INVENTORY_OUTPUT}")

    inventory = pd.read_csv(INVENTORY_OUTPUT)
    filtered = inventory[inventory["source_file"].isin(target_stems)]
    filtered_path = INVENTORY_OUTPUT.parent / "_filtered_inventory.csv"
    filtered.to_csv(filtered_path, index=False)

    results = extractor.extract_from_inventory(
        inventory_csv_path=filtered_path,
        text_dir=TEXT_OUTPUT_DIR,
        output_path=DEADLINES_OUTPUT,
        overwrite=False,
    )

    filtered_path.unlink(missing_ok=True)

    total_rules = sum(len(r.timing_rules) for r in results)
    errors = sum(1 for r in results if r.error)
    print(f"Extraction complete: {len(results)} sections, {total_rules} rules, {errors} errors")


with DAG(
    dag_id="cba_pipeline",
    default_args=default_args,
    description="Process CBAs: extract text, populate metadata, build section inventory, index to Elasticsearch, extract deadlines",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["cba-clock"],
) as dag:

    download = PythonOperator(
        task_id="download_pdfs",
        python_callable=task_download_pdfs,
    )

    extract = PythonOperator(
        task_id="extract_text",
        python_callable=task_extract_text,
    )

    metadata = PythonOperator(
        task_id="populate_cba_metadata",
        python_callable=task_populate_cba_metadata,
    )

    report = PythonOperator(
        task_id="quality_report",
        python_callable=task_quality_report,
    )

    inventory = PythonOperator(
        task_id="section_inventory",
        python_callable=task_section_inventory,
    )

    index = PythonOperator(
        task_id="index_sections",
        python_callable=task_index_sections,
    )

    deadlines = PythonOperator(
        task_id="extract_deadlines",
        python_callable=task_extract_deadlines,
    )

    download >> extract >> metadata >> report >> inventory >> index >> deadlines