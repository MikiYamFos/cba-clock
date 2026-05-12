import os
from dotenv import load_dotenv

load_dotenv()

from pathlib import Path
from typing import Optional
import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from worker.app.es_search import CBASectionSearcher
from worker.app.db import (
    get_all_cbas,
    get_timing_rules_for_cba,
    upsert_timing_rule_correction,
    get_cba_id,
)

app = FastAPI()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RAW_PDF_DIR = Path(
    os.environ.get("RAW_PDF_DIR", "/opt/cba_clock/data/samples/raw_pdfs")
)
TEXT_OUTPUT_DIR = Path(
    os.environ.get("TEXT_OUTPUT_DIR", "/opt/cba_clock/data/processed/extracted_text")
)
AIRFLOW_URL = os.environ.get("AIRFLOW_URL", "http://airflow-apiserver:8080")
AIRFLOW_USER = os.environ.get("AIRFLOW_WWW_USER", "admin")
AIRFLOW_PASS = os.environ.get("AIRFLOW_WWW_PASSWORD", "admin")

searcher = CBASectionSearcher()

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@app.get("/contracts")
def get_contracts():
    try:
        return get_all_cbas()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/contracts/available")
def get_available_contracts():
    try:
        all_pdfs = {p.name for p in RAW_PDF_DIR.glob("*.pdf")}
        pg_contracts = {c["source_file"]: c for c in get_all_cbas()}

        try:
            es_response = searcher.es.search(
                index="cba_sections",
                body={
                    "size": 0,
                    "aggs": {
                        "source_files": {
                            "terms": {"field": "source_file", "size": 1000}
                        }
                    },
                },
            )
            es_files = {
                b["key"].replace(".txt", ".pdf")
                for b in es_response["aggregations"]["source_files"]["buckets"]
            }
        except Exception:
            es_files = set()

        results = []
        for filename in sorted(all_pdfs):
            pg = pg_contracts.get(filename) or pg_contracts.get(
                filename.replace(".pdf", ".txt")
            )
            in_es = filename in es_files

            if pg and in_es:
                status = "indexed"
            elif pg:
                status = "metadata_only"
            else:
                status = "unprocessed"

            results.append(
                {
                    "filename": filename,
                    "status": status,
                    "employer_name": pg.get("employer_name") if pg else None,
                    "union_name": pg.get("union_name") if pg else None,
                    "union_local": pg.get("union_local") if pg else None,
                    "location": pg.get("location") if pg else None,
                    "version_label": pg.get("version_label") if pg else None,
                    "expiration_date": pg.get("expiration_date") if pg else None,
                    "document_type": pg.get("document_type") if pg else None,
                    "last_processed_at": (
                        str(pg.get("last_processed_at") or "")[:19] if pg else None
                    ),
                }
            )

        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/contracts/process")
async def process_contracts(
    filenames: list[str],
    overwrite: bool = Query(default=False),
):
    if not filenames:
        raise HTTPException(status_code=400, detail="No filenames provided.")

    dag_run_response = await _trigger_dag(filenames=filenames, overwrite=overwrite)

    return {
        "filenames": filenames,
        "overwrite": overwrite,
        "dag_run_id": dag_run_response.get("dag_run_id"),
        "message": f"Pipeline triggered for {len(filenames)} file(s).",
    }


# ---------------------------------------------------------------------------
# Timing rules
# ---------------------------------------------------------------------------


@app.get("/timing-rules/{source_file}")
def get_timing_rules(source_file: str):
    """
    Return all timing rules for a contract with their corrections.
    source_file is the PDF filename e.g. dol_5_VERIZON_DELAWARE_INC_VERIZON_SERVICES_CORP.pdf
    """
    try:
        rules = get_timing_rules_for_cba(source_file)
        if not rules:
            return []
        for rule in rules:
            for key, val in rule.items():
                if hasattr(val, "isoformat"):
                    rule[key] = val.isoformat()
        return rules
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sections/{source_file}/{article_number}/context")
def get_section_context(
    source_file: str, article_number: str, quote: str = Query(default="")
):
    """
    Return the full text of a contract section with the quote's position marked.

    The source_file is the PDF filename. We look up start_char/end_char from
    Postgres, read the corresponding .txt file, extract the section, and find
    the quote within it.

    Returns:
      - section_text: the full section text
      - quote_start: character offset of the quote within section_text (or -1 if not found)
      - quote_end: end character offset
      - article_title: the article title
    """
    try:
        # Convert PDF filename to txt filename for the text file
        txt_filename = source_file.replace(".pdf", ".txt")
        text_path = TEXT_OUTPUT_DIR / txt_filename

        if not text_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Text file not found for {source_file}. Has the pipeline run?",
            )

        # Get section boundaries from Postgres
        import psycopg2.extras
        from worker.app.db import get_connection

        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT cs.start_char, cs.end_char, cs.article_title
                    FROM cba_section cs
                    JOIN cba c ON c.id = cs.cba_id
                    WHERE c.source_file = %s AND cs.article_number = %s
                """,
                    (source_file, article_number),
                )
                row = cur.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Section {article_number} not found for {source_file}",
            )

        full_text = text_path.read_text(encoding="utf-8")
        section_text = full_text[row["start_char"] : row["end_char"]].strip()

        # Find the quote within the section text
        quote_start = -1
        quote_end = -1
        if quote:
            # Try exact match first
            idx = section_text.lower().find(quote.lower()[:50])
            if idx != -1:
                quote_start = idx
                quote_end = idx + len(quote)

        return {
            "section_text": section_text,
            "article_title": row["article_title"],
            "quote_start": quote_start,
            "quote_end": quote_end,
            "quote": quote,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CorrectionRequest(BaseModel):
    action: Optional[str] = None
    trigger: Optional[str] = None
    offset_days: Optional[int] = None
    unit: Optional[str] = None
    party: Optional[str] = None
    quote: Optional[str] = None
    correction_note: Optional[str] = None
    corrected_by: Optional[str] = None


@app.post("/timing-rules/{timing_rule_id}/correction")
def save_correction(timing_rule_id: int, body: CorrectionRequest):
    """
    Save a human correction to a timing rule.
    Only send the fields that are wrong — null means same as original.
    The original Claude extraction is never modified.
    """
    try:
        correction_id = upsert_timing_rule_correction(
            timing_rule_id=timing_rule_id,
            action=body.action,
            trigger=body.trigger,
            offset_days=body.offset_days,
            unit=body.unit,
            party=body.party,
            quote=body.quote,
            correction_note=body.correction_note,
            corrected_by=body.corrected_by,
        )
        return {"correction_id": correction_id, "timing_rule_id": timing_rule_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@app.post("/upload")
async def upload_cba(
    file: UploadFile = File(...),
    overwrite: bool = Query(default=False),
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_PDF_DIR / file.filename

    if dest.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"{file.filename} already exists. Pass overwrite=true to replace it.",
        )

    contents = await file.read()
    dest.write_bytes(contents)
    dag_run_response = await _trigger_dag(filename=file.filename, overwrite=overwrite)

    return {
        "filename": file.filename,
        "size_bytes": len(contents),
        "overwrite": overwrite,
        "dag_run_id": dag_run_response.get("dag_run_id"),
        "message": f"Upload successful. Pipeline triggered for {file.filename}.",
    }


@app.post("/upload/bulk")
async def upload_bulk(
    files: list[UploadFile] = File(...),
    overwrite: bool = Query(default=False),
):
    saved = []
    skipped = []
    RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)

    for file in files:
        if not file.filename.endswith(".pdf"):
            skipped.append({"filename": file.filename, "reason": "not a PDF"})
            continue
        dest = RAW_PDF_DIR / file.filename
        if dest.exists() and not overwrite:
            skipped.append({"filename": file.filename, "reason": "already exists"})
            continue
        contents = await file.read()
        dest.write_bytes(contents)
        saved.append(file.filename)

    if not saved:
        raise HTTPException(status_code=400, detail="No new files to process.")

    dag_run_response = await _trigger_dag(filenames=saved, overwrite=overwrite)
    return {
        "saved": saved,
        "skipped": skipped,
        "dag_run_id": dag_run_response.get("dag_run_id"),
        "message": f"Pipeline triggered for {len(saved)} file(s).",
    }


@app.post("/reprocess/{filename}")
async def reprocess(filename: str):
    dest = RAW_PDF_DIR / filename
    if not dest.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found.")
    dag_run_response = await _trigger_dag(filename=filename, overwrite=True)
    return {
        "filename": filename,
        "dag_run_id": dag_run_response.get("dag_run_id"),
        "message": f"Re-processing triggered for {filename}.",
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@app.get("/search")
def search_all(
    q: str = Query(min_length=1),
    phrase: bool = False,
    size: int = Query(default=20, ge=1, le=100),
    from_: int = Query(default=0, alias="from", ge=0),
):
    result = searcher.search_cross_cba(
        query=q, size=size, from_=from_, phrase_match=phrase
    )
    return result


@app.get("/search/{source_file}")
def search_within(
    source_file: str,
    q: str = Query(min_length=1),
    phrase: bool = False,
    size: int = Query(default=20, ge=1, le=100),
    from_: int = Query(default=0, alias="from", ge=0),
):
    result = searcher.search_within_cba(
        query=q, source_file=source_file, size=size, from_=from_, phrase_match=phrase
    )
    return result


# ---------------------------------------------------------------------------
# Airflow DAG trigger — Airflow 3.x /api/v2
# ---------------------------------------------------------------------------


async def _trigger_dag(
    filename: str | None = None,
    filenames: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    conf: dict = {"overwrite": overwrite}
    if filename:
        conf["filename"] = filename
    if filenames:
        conf["filenames"] = filenames

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{AIRFLOW_URL}/api/v2/dags/cba_pipeline/dagRuns",
            json={"conf": conf},
            auth=(AIRFLOW_USER, AIRFLOW_PASS),
            timeout=10.0,
        )

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Airflow trigger failed: {response.status_code} {response.text}",
        )
    return response.json()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
