"""
worker/app/es_search.py

Search and highlight logic for CBA Clock.

Two primary search modes:
  1. Cross-CBA search  — find a phrase or concept across all indexed contracts
  2. Within-CBA search — restrict results to a single source_file

Both modes return highlighted fragments so the UI can render matched phrases
in context without loading the full section text.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from elasticsearch import Elasticsearch
from dotenv import load_dotenv

load_dotenv()

INDEX_NAME = "cba_sections"

# Number of highlight fragments returned per section hit.
# Each fragment is ~150 chars and contains the matched phrase(s).
HIGHLIGHT_FRAGMENT_SIZE = 150
HIGHLIGHT_FRAGMENT_COUNT = 3


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HighlightFragment:
    """A snippet of text with matched terms wrapped in <em> tags."""

    fragment: str


@dataclass
class SectionHit:
    """One matching contract section."""

    doc_id: str
    source_file: str
    article_number: str
    article_title: str
    labels: list[str]
    start_char: int
    end_char: int
    score: float
    highlights: list[HighlightFragment] = field(default_factory=list)


@dataclass
class SearchResponse:
    total_hits: int
    hits: list[SectionHit]
    query: str
    source_file_filter: str | None = None


# ---------------------------------------------------------------------------
# Searcher
# ---------------------------------------------------------------------------


class CBASectionSearcher:
    """
    Full-text and phrase search over indexed CBA sections.

    Highlight tags default to <em>/<em> so they render safely in HTML.
    Override pre_tag / post_tag if you need different markup (e.g. <mark>).
    """

    def __init__(
        self,
        es_url: str | None = None,
        pre_tag: str = "<em>",
        post_tag: str = "</em>",
    ) -> None:
        url = es_url or os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
        self.es = Elasticsearch(url)
        self.pre_tag = pre_tag
        self.post_tag = post_tag

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        source_file: str | None = None,
        labels: list[str] | None = None,
        size: int = 20,
        from_: int = 0,
        phrase_match: bool = False,
    ) -> SearchResponse:
        """
        Search sections by query string.

        Args:
            query:        User-supplied search string.
            source_file:  If set, restrict results to this contract file.
            labels:       If set, restrict results to sections with ALL listed labels.
            size:         Page size (max results to return).
            from_:        Offset for pagination.
            phrase_match: If True, treat query as an exact phrase (match_phrase).
                          If False, use multi_match across text + article_title.

        Returns:
            SearchResponse with scored, highlighted SectionHit objects.
        """
        es_query = self._build_query(
            query=query,
            source_file=source_file,
            labels=labels,
            phrase_match=phrase_match,
        )

        highlight = self._build_highlight()

        response = self.es.search(
            index=INDEX_NAME,
            body={
                "query": es_query,
                "highlight": highlight,
                "size": size,
                "from": from_,
                "_source": [
                    "source_file",
                    "article_number",
                    "article_title",
                    "labels",
                    "start_char",
                    "end_char",
                ],
            },
        )

        return self._parse_response(
            response, query=query, source_file_filter=source_file
        )

    def search_cross_cba(
        self,
        query: str,
        labels: list[str] | None = None,
        size: int = 20,
        from_: int = 0,
        phrase_match: bool = False,
    ) -> SearchResponse:
        """Find matching sections across ALL indexed contracts."""
        return self.search(
            query=query,
            labels=labels,
            size=size,
            from_=from_,
            phrase_match=phrase_match,
        )

    def search_within_cba(
        self,
        query: str,
        source_file: str,
        labels: list[str] | None = None,
        size: int = 20,
        from_: int = 0,
        phrase_match: bool = False,
    ) -> SearchResponse:
        """Find matching sections within a single contract."""
        return self.search(
            query=query,
            source_file=source_file,
            labels=labels,
            size=size,
            from_=from_,
            phrase_match=phrase_match,
        )

    def suggest_phrases(self, prefix: str, source_file: str | None = None) -> list[str]:
        """
        Return completion suggestions for a search prefix.
        Useful for powering a search-as-you-type UI.

        Uses a simple prefix query against article_title.keyword.
        For more sophisticated autocomplete, add a completion field to the index.
        """
        must: list[dict] = [{"prefix": {"article_title.keyword": {"value": prefix}}}]
        if source_file:
            must.append({"term": {"source_file": source_file}})

        response = self.es.search(
            index=INDEX_NAME,
            body={
                "query": {"bool": {"must": must}},
                "size": 10,
                "_source": ["article_title"],
                "collapse": {"field": "article_title.keyword"},
            },
        )
        return [hit["_source"]["article_title"] for hit in response["hits"]["hits"]]

    # ------------------------------------------------------------------
    # Query builders
    # ------------------------------------------------------------------

    def _build_query(
        self,
        query: str,
        source_file: str | None,
        labels: list[str] | None,
        phrase_match: bool,
    ) -> dict[str, Any]:
        filters: list[dict] = []

        if source_file:
            filters.append({"term": {"source_file": source_file}})

        if labels:
            for label in labels:
                filters.append({"term": {"labels": label}})

        if phrase_match:
            # Exact phrase search on the `.exact` sub-field (no stemming).
            # Falls back to the stemmed `text` field in multi_match for ranking.
            text_query: dict = {
                "multi_match": {
                    "query": query,
                    "type": "phrase",
                    "fields": ["text.exact", "article_title"],
                }
            }
        else:
            # Best-fields multi_match across stemmed text + article title.
            # `cross_fields` would also work well for multi-word queries.
            text_query = {
                "multi_match": {
                    "query": query,
                    "type": "best_fields",
                    "fields": ["text^1", "article_title^2"],
                    "fuzziness": "AUTO",
                    "minimum_should_match": "75%",
                }
            }

        if filters:
            return {
                "bool": {
                    "must": text_query,
                    "filter": filters,
                }
            }
        return text_query

    def _build_highlight(self) -> dict[str, Any]:
        return {
            "pre_tags": [self.pre_tag],
            "post_tags": [self.post_tag],
            "number_of_fragments": HIGHLIGHT_FRAGMENT_COUNT,
            "fragment_size": HIGHLIGHT_FRAGMENT_SIZE,
            "fields": {
                # Use the .exact sub-field for highlights so the returned
                # snippets reflect the original contract wording exactly.
                "text.exact": {},
                "article_title": {"number_of_fragments": 0},  # always return full title
            },
            "order": "score",
        }

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        response: dict,
        query: str,
        source_file_filter: str | None,
    ) -> SearchResponse:
        raw_hits = response["hits"]["hits"]
        total = response["hits"]["total"]["value"]

        hits: list[SectionHit] = []
        for raw in raw_hits:
            src = raw["_source"]
            raw_highlights = raw.get("highlight", {})

            # Collect highlight fragments from both fields
            fragments: list[HighlightFragment] = []
            for field_frags in raw_highlights.values():
                for frag in field_frags:
                    fragments.append(HighlightFragment(fragment=frag))

            hits.append(
                SectionHit(
                    doc_id=raw["_id"],
                    source_file=src["source_file"],
                    article_number=src["article_number"],
                    article_title=src["article_title"],
                    labels=src.get("labels", []),
                    start_char=src["start_char"],
                    end_char=src["end_char"],
                    score=raw["_score"],
                    highlights=fragments,
                )
            )

        return SearchResponse(
            total_hits=total,
            hits=hits,
            query=query,
            source_file_filter=source_file_filter,
        )
