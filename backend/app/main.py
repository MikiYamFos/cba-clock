import os
from dotenv import load_dotenv

load_dotenv()

from pathlib import Path
import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from worker.app.es_search import CBASectionSearcher
from worker.app.db import get_all_cbas

app = FastAPI()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RAW_PDF_DIR = Path(
    os.environ.get("RAW_PDF_DIR", "/opt/cba_clock/data/samples/raw_pdfs")
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
# Airflow DAG trigger — uses Airflow 3.x /api/v2 endpoint
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
