"""
worker/app/db.py

Postgres connection and writes for CBA Clock.

Handles:
  - Writing cba records from pipeline metadata lookup
  - Updating last_processed_at timestamps
  - Reading cba records for the contracts browser in the UI
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from worker.app.metadata_lookup import ContractMetadata

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB"),
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def upsert_cba(metadata: ContractMetadata) -> int:
    """
    Insert or update a cba record from pipeline metadata.
    Uses source_file as the unique key — if a record exists for this file,
    updates the metadata and last_processed_at. If not, inserts a new record.

    Note: bargaining_unit_id is nullable here because we don't have the full
    corporate hierarchy yet. It will be linked later when the user associates
    the contract with a bargaining unit in the UI.

    Returns the cba.id of the inserted or updated record.
    """
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cba (
                    bargaining_unit_id,
                    source_file,
                    document_type,
                    version_label,
                    expiration_date,
                    is_current,
                    last_processed_at
                ) VALUES (
                    NULL,
                    %(source_file)s,
                    %(document_type)s,
                    %(version_label)s,
                    %(expiration_date)s,
                    TRUE,
                    %(last_processed_at)s
                )
                ON CONFLICT (source_file)
                DO UPDATE SET
                    version_label       = EXCLUDED.version_label,
                    expiration_date     = EXCLUDED.expiration_date,
                    last_processed_at   = EXCLUDED.last_processed_at
                RETURNING id
            """,
                {
                    "source_file": metadata.filename,
                    "document_type": metadata.document_type,
                    "version_label": metadata.version_label,
                    "expiration_date": metadata.expiration_date,
                    "last_processed_at": now,
                },
            )
            row = cur.fetchone()
            conn.commit()
            return row[0]


def update_last_processed(source_file: str) -> None:
    """Update last_processed_at for a contract that was reprocessed."""
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cba SET last_processed_at = %s WHERE source_file = %s
            """,
                (now, source_file),
            )
            conn.commit()


def get_all_cbas() -> list[dict]:
    """
    Return all CBA records for the contracts browser in the UI.
    Includes employer and union info from the metadata lookup since
    those aren't stored in Postgres yet (no bargaining unit linked).
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                            SELECT
                                id,
                                source_file,
                                employer_name,
                                union_name,
                                union_local,
                                location,
                                document_type,
                                version_label,
                                expiration_date,
                                effective_start_date,
                                effective_end_date,
                                is_current,
                                last_processed_at
                            FROM cba
                            ORDER BY last_processed_at DESC NULLS LAST
                        """)
            return [dict(row) for row in cur.fetchall()]
