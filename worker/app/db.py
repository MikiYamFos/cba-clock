"""
worker/app/db.py

Postgres connection and writes for CBA Clock.

Handles:
  - Writing cba records from pipeline metadata lookup
  - Writing cba_section and timing_rule records from Claude extraction
  - Reading timing rules with corrections for the review UI
  - Reading cba records for the contracts browser
"""

from __future__ import annotations
from dotenv import load_dotenv
import os
from pathlib import Path
load_dotenv()
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


from worker.app.metadata_lookup import ContractMetadata


PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/opt/cba_clock"))

def get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB"),
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


# ---------------------------------------------------------------------------
# CBA
# ---------------------------------------------------------------------------


def upsert_cba(metadata: ContractMetadata) -> int:
    """
    Insert or update a cba record from pipeline metadata.
    Uses source_file as the unique key.
    Returns the cba.id.
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
                    employer_name,
                    union_name,
                    union_local,
                    location,
                    version_label,
                    expiration_date,
                    is_current,
                    last_processed_at
                ) VALUES (
                    NULL,
                    %(source_file)s,
                    %(document_type)s,
                    %(employer_name)s,
                    %(union_name)s,
                    %(union_local)s,
                    %(location)s,
                    %(version_label)s,
                    %(expiration_date)s,
                    TRUE,
                    %(last_processed_at)s
                )
                ON CONFLICT (source_file)
                DO UPDATE SET
                    employer_name       = EXCLUDED.employer_name,
                    union_name          = EXCLUDED.union_name,
                    union_local         = EXCLUDED.union_local,
                    location            = EXCLUDED.location,
                    version_label       = EXCLUDED.version_label,
                    expiration_date     = EXCLUDED.expiration_date,
                    last_processed_at   = EXCLUDED.last_processed_at
                RETURNING id
                """,
                {
                    "source_file": metadata.filename,
                    "document_type": metadata.document_type,
                    "employer_name": metadata.employer_name,
                    "union_name": metadata.union_name,
                    "union_local": metadata.union_local,
                    "location": metadata.location,
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
                "UPDATE cba SET last_processed_at = %s WHERE source_file = %s",
                (now, source_file),
            )
            conn.commit()


def get_cba_id(source_file: str) -> int | None:
    """Return the cba.id for a given source_file, or None if not found."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cba WHERE source_file = %s", (source_file,))
            row = cur.fetchone()
            return row[0] if row else None


def get_all_cbas() -> list[dict]:
    """Return all CBA records for the contracts browser."""
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


# ---------------------------------------------------------------------------
# CBA sections
# ---------------------------------------------------------------------------


def upsert_cba_section(
    cba_id: int,
    article_number: str,
    article_title: str,
    start_char: int,
    end_char: int,
    labels: list[str],
) -> int:
    """
    Insert or update a cba_section record.
    Uses (cba_id, article_number) as the unique key.
    Returns the cba_section.id.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cba_section (
                    cba_id, article_number, article_title,
                    start_char, end_char, labels
                ) VALUES (
                    %(cba_id)s, %(article_number)s, %(article_title)s,
                    %(start_char)s, %(end_char)s, %(labels)s
                )
                ON CONFLICT (cba_id, article_number)
                DO UPDATE SET
                    article_title = EXCLUDED.article_title,
                    start_char    = EXCLUDED.start_char,
                    end_char      = EXCLUDED.end_char,
                    labels        = EXCLUDED.labels
                RETURNING id
                """,
                {
                    "cba_id": cba_id,
                    "article_number": article_number,
                    "article_title": article_title,
                    "start_char": start_char,
                    "end_char": end_char,
                    "labels": labels,
                },
            )
            row = cur.fetchone()
            conn.commit()
            return row[0]


# ---------------------------------------------------------------------------
# Timing rules
# ---------------------------------------------------------------------------


def insert_timing_rule(
    cba_section_id: int,
    rule_id: str | None,
    action: str,
    trigger: str,
    offset_days: int,
    unit: str,
    party: str | None,
    quote: str | None,
    is_ambiguous: bool = False,
    ambiguity_note: str | None = None,
) -> int:
    """
    Insert a timing rule extracted by Claude.
    Timing rules are immutable after creation — never updated, only inserted.
    If a rule with this (cba_section_id, rule_id) already exists, returns
    the existing id without inserting a duplicate.
    Returns the timing_rule.id.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            if rule_id:
                cur.execute(
                    """
                    SELECT id FROM timing_rule
                    WHERE cba_section_id = %s AND rule_id = %s
                    """,
                    (cba_section_id, rule_id),
                )
                existing = cur.fetchone()
                if existing:
                    return existing[0]

            cur.execute(
                """
                INSERT INTO timing_rule (
                    cba_section_id, rule_id, action, trigger,
                    offset_days, unit, party, quote,
                    is_ambiguous, ambiguity_note
                ) VALUES (
                    %(cba_section_id)s, %(rule_id)s, %(action)s, %(trigger)s,
                    %(offset_days)s, %(unit)s, %(party)s, %(quote)s,
                    %(is_ambiguous)s, %(ambiguity_note)s
                )
                RETURNING id
                """,
                {
                    "cba_section_id": cba_section_id,
                    "rule_id": rule_id,
                    "action": action,
                    "trigger": trigger,
                    "offset_days": offset_days,
                    "unit": unit,
                    "party": party,
                    "quote": quote,
                    "is_ambiguous": is_ambiguous,
                    "ambiguity_note": ambiguity_note,
                },
            )
            row = cur.fetchone()
            conn.commit()
            return row[0]


def upsert_timing_rule_correction(
    timing_rule_id: int,
    action: str | None = None,
    trigger: str | None = None,
    offset_days: int | None = None,
    unit: str | None = None,
    party: str | None = None,
    quote: str | None = None,
    correction_note: str | None = None,
    corrected_by: str | None = None,
) -> int:
    """
    Insert or update a human correction to a timing rule.
    Only one active correction per timing rule (UNIQUE constraint).
    NULL fields mean "same as original" — only store what changed.
    Returns the timing_rule_correction.id.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO timing_rule_correction (
                    timing_rule_id, action, trigger, offset_days,
                    unit, party, quote, correction_note, corrected_by
                ) VALUES (
                    %(timing_rule_id)s, %(action)s, %(trigger)s, %(offset_days)s,
                    %(unit)s, %(party)s, %(quote)s, %(correction_note)s, %(corrected_by)s
                )
                ON CONFLICT (timing_rule_id)
                DO UPDATE SET
                    action          = EXCLUDED.action,
                    trigger         = EXCLUDED.trigger,
                    offset_days     = EXCLUDED.offset_days,
                    unit            = EXCLUDED.unit,
                    party           = EXCLUDED.party,
                    quote           = EXCLUDED.quote,
                    correction_note = EXCLUDED.correction_note,
                    corrected_by    = EXCLUDED.corrected_by,
                    corrected_at    = NOW()
                RETURNING id
                """,
                {
                    "timing_rule_id": timing_rule_id,
                    "action": action,
                    "trigger": trigger,
                    "offset_days": offset_days,
                    "unit": unit,
                    "party": party,
                    "quote": quote,
                    "correction_note": correction_note,
                    "corrected_by": corrected_by,
                },
            )
            row = cur.fetchone()
            conn.commit()
            return row[0]


def get_timing_rules_for_cba(source_file: str) -> list[dict]:
    """
    Return all timing rules for a CBA, joined with their corrections.
    Shows both original Claude extraction and any human correction.
    COALESCE gives effective values — correction takes precedence over original.
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    tr.id                   AS timing_rule_id,
                    tr.rule_id,
                    tr.action               AS original_action,
                    tr.trigger              AS original_trigger,
                    tr.offset_days          AS original_offset_days,
                    tr.unit                 AS original_unit,
                    tr.party                AS original_party,
                    tr.quote                AS original_quote,
                    tr.is_ambiguous,
                    tr.ambiguity_note,
                    tr.created_at           AS extracted_at,
                    cs.article_number,
                    cs.article_title,
                    trc.id                  AS correction_id,
                    trc.action              AS corrected_action,
                    trc.trigger             AS corrected_trigger,
                    trc.offset_days         AS corrected_offset_days,
                    trc.unit                AS corrected_unit,
                    trc.party               AS corrected_party,
                    trc.quote               AS corrected_quote,
                    trc.correction_note,
                    trc.corrected_by,
                    trc.corrected_at,
                    COALESCE(trc.action,      tr.action)      AS effective_action,
                    COALESCE(trc.trigger,     tr.trigger)     AS effective_trigger,
                    COALESCE(trc.offset_days, tr.offset_days) AS effective_offset_days,
                    COALESCE(trc.unit,        tr.unit)        AS effective_unit,
                    COALESCE(trc.party,       tr.party)       AS effective_party,
                    COALESCE(trc.quote,       tr.quote)       AS effective_quote
                FROM timing_rule tr
                JOIN cba_section cs ON cs.id = tr.cba_section_id
                JOIN cba c ON c.id = cs.cba_id
                LEFT JOIN timing_rule_correction trc ON trc.timing_rule_id = tr.id
                WHERE c.source_file = %s
                ORDER BY cs.article_number, tr.rule_id
                """,
                (source_file,),
            )
            return [dict(row) for row in cur.fetchall()]
