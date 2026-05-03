"""
worker/app/es_client.py

Elasticsearch index management and document indexing for CBA Clock.

Index strategy:
  - One index: `cba_sections`
  - Each document = one ContractSection
  - Separate `text` field (full-text search) and `text_exact` sub-field (phrase/highlight)
  - Metadata fields allow filtering by contract, article, and label
"""

from __future__ import annotations

import logging
import os
from typing import Any
from dotenv import load_dotenv

from elasticsearch import Elasticsearch, helpers

from worker.app.section_extraction import ContractSection


load_dotenv()

logger = logging.getLogger(__name__)

INDEX_NAME = "cba_sections"

# ---------------------------------------------------------------------------
# Index mapping
# ---------------------------------------------------------------------------
# `text` uses the built-in `english` analyzer for stemmed full-text search.
# `text.exact` is a sub-field with no stemming, used for phrase highlighting
# so that snippets come back with the original contract wording intact.
# ---------------------------------------------------------------------------

INDEX_MAPPING: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "cba_standard": {
                    # Custom analyzer: lowercase + standard tokenization.
                    # Keeps numbers (e.g. "10") as searchable tokens.
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            # --- Core text fields ---
            "text": {
                "type": "text",
                "analyzer": "english",  # stemmed search
                "fields": {
                    "exact": {
                        "type": "text",
                        "analyzer": "cba_standard",  # no stemming → clean highlights
                    }
                },
            },
            "article_title": {
                "type": "text",
                "analyzer": "english",
                "fields": {
                    "keyword": {"type": "keyword"}  # for aggregations / exact filter
                },
            },
            # --- Identity / filter fields ---
            "source_file": {"type": "keyword"},
            "article_number": {"type": "keyword"},
            "labels": {"type": "keyword"},  # multi-value; one keyword per label
            # --- Character offsets (for jumping to position in raw text) ---
            "start_char": {"type": "integer"},
            "end_char": {"type": "integer"},
        }
    },
}


class CBASectionIndexer:
    """Manages the `cba_sections` index and bulk-indexes ContractSection objects."""

    def __init__(self, es_url: str | None = None) -> None:
        url = es_url or os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
        self.es = Elasticsearch(url)
        logger.info("Elasticsearch client connected to %s", url)

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    def ensure_index(self, recreate: bool = False) -> None:
        """Create the index if it doesn't exist. Pass recreate=True to drop and rebuild."""
        if recreate and self.es.indices.exists(index=INDEX_NAME):
            self.es.indices.delete(index=INDEX_NAME)
            logger.info("Dropped existing index '%s'", INDEX_NAME)

        if not self.es.indices.exists(index=INDEX_NAME):
            self.es.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
            logger.info("Created index '%s'", INDEX_NAME)
        else:
            logger.info("Index '%s' already exists", INDEX_NAME)

    def delete_by_source(self, source_file: str) -> int:
        """Remove all documents for a given source file (for re-indexing a single contract)."""
        response = self.es.delete_by_query(
            index=INDEX_NAME,
            body={"query": {"term": {"source_file": source_file}}},
            refresh=True,
        )
        deleted = response.get("deleted", 0)
        logger.info("Deleted %d documents for source_file='%s'", deleted, source_file)
        return deleted

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_sections(
        self,
        sections: list[ContractSection],
        labels_by_section: dict[tuple[str, str], list[str]] | None = None,
        overwrite_source: bool = False,
    ) -> tuple[int, int]:
        """
        Bulk-index a list of ContractSection objects.

        Args:
            sections: Extracted contract sections.
            labels_by_section: Optional mapping of (source_file, article_number) → labels.
                               If omitted, labels are left empty.
            overwrite_source: If True, delete existing docs for each source file first.

        Returns:
            (success_count, error_count)
        """
        if not sections:
            return 0, 0

        if overwrite_source:
            sources = {s.source_file for s in sections}
            for source in sources:
                self.delete_by_source(source)

        actions = []
        for section in sections:
            labels: list[str] = []
            if labels_by_section:
                labels = labels_by_section.get(
                    (section.source_file, section.article_number), []
                )

            doc_id = f"{section.source_file}::{section.article_number}"
            actions.append(
                {
                    "_index": INDEX_NAME,
                    "_id": doc_id,
                    "_source": {
                        "source_file": section.source_file,
                        "article_number": section.article_number,
                        "article_title": section.article_title,
                        "text": section.text,
                        "start_char": section.start_char,
                        "end_char": section.end_char,
                        "labels": labels,
                    },
                }
            )

        success, errors = helpers.bulk(
            self.es,
            actions,
            raise_on_error=False,
            stats_only=False,
        )
        error_count = len(errors) if isinstance(errors, list) else 0

        if error_count:
            logger.warning("%d bulk indexing errors", error_count)
            for err in errors[:5]:
                logger.warning("  %s", err)

        logger.info(
            "Indexed %d sections (%d errors) from %d source files",
            success,
            error_count,
            len({s.source_file for s in sections}),
        )
        return success, error_count

    def index_section(
        self, section: ContractSection, labels: list[str] | None = None
    ) -> None:
        """Index or update a single section."""
        doc_id = f"{section.source_file}::{section.article_number}"
        self.es.index(
            index=INDEX_NAME,
            id=doc_id,
            document={
                "source_file": section.source_file,
                "article_number": section.article_number,
                "article_title": section.article_title,
                "text": section.text,
                "start_char": section.start_char,
                "end_char": section.end_char,
                "labels": labels or [],
            },
            refresh=True,
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def count(self) -> int:
        return self.es.count(index=INDEX_NAME)["count"]

    def health(self) -> str:
        return self.es.cluster.health()["status"]
