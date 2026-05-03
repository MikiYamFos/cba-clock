"""
worker/app/metadata_lookup.py

Looks up contract metadata from the OPM and DOL source CSVs by filename.
Returns a unified dict regardless of which source the contract came from.

Used by the pipeline to populate the Postgres cba table with real metadata
instead of leaving dates and employer names blank.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path("/opt/cba_clock")
OPM_CSV = PROJECT_ROOT / "data" / "sample_sources" / "opm_cba_sources.csv"
DOL_CSV = PROJECT_ROOT / "data" / "sample_sources" / "dol_cba_sources.csv"


@dataclass
class ContractMetadata:
    filename: str
    employer_name: str | None
    union_name: str | None
    union_local: str | None
    location: str | None
    expiration_date: str | None  # ISO date string e.g. "2025-06-29"
    exp_year: int | None
    source: str  # "opm" or "dol"
    document_type: str = "ratified"  # all downloaded contracts are ratified finals
    version_label: str | None = None  # e.g. "2023-2026" derived from dates if available


def lookup(filename: str) -> ContractMetadata | None:
    """
    Look up metadata for a contract by filename.
    Checks OPM CSV first, then DOL CSV.
    Returns None if the file isn't found in either source.
    """
    # Normalize filename — strip path, keep just the name
    filename = Path(filename).name

    result = _lookup_opm(filename)
    if result:
        return result

    result = _lookup_dol(filename)
    if result:
        return result

    return None


def _lookup_opm(filename: str) -> ContractMetadata | None:
    if not OPM_CSV.exists():
        return None

    df = pd.read_csv(OPM_CSV)

    # OPM filenames sometimes have spaces instead of underscores
    match = df[df["filename"] == filename]
    if match.empty:
        return None

    row = match.iloc[0]

    expiration_date = None
    exp_year = None
    version_label = None

    raw_date = row.get("expiration_date", "")
    if pd.notna(raw_date) and raw_date:
        try:
            parsed = pd.to_datetime(raw_date)
            expiration_date = parsed.date().isoformat()
            exp_year = parsed.year
            version_label = str(parsed.year)
        except Exception:
            pass

    return ContractMetadata(
        filename=filename,
        employer_name=str(row.get("agency", "")) or None,
        union_name=str(row.get("union", "")) or None,
        union_local=str(row.get("local", "")) or None,
        location=None,  # OPM doesn't have location
        expiration_date=expiration_date,
        exp_year=exp_year,
        source="opm",
        version_label=version_label,
    )


def _lookup_dol(filename: str) -> ContractMetadata | None:
    if not DOL_CSV.exists():
        return None

    df = pd.read_csv(DOL_CSV)
    match = df[df["filename"] == filename]
    if match.empty:
        return None

    row = match.iloc[0]

    expiration_date = None
    exp_year = None
    version_label = None

    # DOL uses Unix timestamp in milliseconds
    raw_ts = row.get("exp_date_ts", None)
    raw_year = row.get("exp_year", None)

    if pd.notna(raw_ts) and raw_ts:
        try:
            parsed = datetime.fromtimestamp(int(raw_ts) / 1000, tz=timezone.utc)
            expiration_date = parsed.date().isoformat()
            exp_year = parsed.year
        except Exception:
            pass

    if pd.notna(raw_year) and raw_year:
        try:
            exp_year = int(raw_year)
            version_label = str(exp_year)
        except Exception:
            pass

    return ContractMetadata(
        filename=filename,
        employer_name=str(row.get("emp_name", "")) or None,
        union_name=str(row.get("union_name", "")) or None,
        union_local=None,  # DOL doesn't have local number
        location=str(row.get("location", "")) or None,
        expiration_date=expiration_date,
        exp_year=exp_year,
        source="dol",
        version_label=version_label,
    )
