from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
import pandas as pd

import anthropic

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a legal analyst specializing in collective bargaining agreements.
Your job is to extract timing rules from contract sections — deadlines, notice periods,
response windows, and any other time-bound obligations.

Return ONLY valid JSON. No preamble, no explanation, no markdown fences.

For each timing rule you find, extract:
- rule_id: article number + sequential index (e.g. "12-1", "12-2")
- action: what must happen (e.g. "file grievance", "employer response")
- trigger: what starts the clock (e.g. "occurrence", "receipt of grievance")
- offset_days: the number (e.g. 10)
- unit: one of calendar_days, working_days, business_days, weeks, months, hours
- party: who must act (e.g. "employee", "employer", "union", "agency")
- quote: the exact phrase in the text that contains the timing rule (under 30 words)

If no timing rules exist in the section, return an empty timing_rules array.
If a rule is ambiguous, include it with a note in the notes field.

Return this exact structure:
{
  "timing_rules": [...],
  "notes": "optional notes about ambiguous or complex rules"
}"""


USER_PROMPT_TEMPLATE = """Extract all timing rules from this contract section.

Source: {source_file}
Article {article_number}: {article_title}

---
{text}
---"""


@dataclass
class ExtractionResult:
    source_file: str
    article_number: str
    article_title: str
    labels: list[str]
    extraction_model: str
    timing_rules: list[dict] = field(default_factory=list)
    notes: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "article_number": self.article_number,
            "article_title": self.article_title,
            "labels": self.labels,
            "extraction_model": self.extraction_model,
            "timing_rules": self.timing_rules,
            "notes": self.notes,
            "error": self.error,
        }


class DeadlineExtractor:
    RELEVANT_LABELS = {"grievance_procedure", "arbitration", "contract_term"}

    def __init__(self, max_section_chars: int = 12_000) -> None:
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.max_section_chars = max_section_chars

    def should_process(self, labels: list[str]) -> bool:
        return bool(self.RELEVANT_LABELS.intersection(set(labels)))

    def extract(
        self,
        source_file: str,
        article_number: str,
        article_title: str,
        labels: list[str],
        text: str,
    ) -> ExtractionResult:
        result = ExtractionResult(
            source_file=source_file,
            article_number=article_number,
            article_title=article_title,
            labels=labels,
            extraction_model=MODEL,
        )

        if len(text) > self.max_section_chars:
            text = text[: self.max_section_chars] + "\n\n[TRUNCATED]"

        user_prompt = USER_PROMPT_TEMPLATE.format(
            source_file=source_file,
            article_number=article_number,
            article_title=article_title,
            text=text,
        )

        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw = response.content[0].text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            parsed = json.loads(raw)
            result.timing_rules = parsed.get("timing_rules", [])
            result.notes = parsed.get("notes", "")

        except json.JSONDecodeError as e:
            result.error = f"JSON parse error: {e} | raw: {raw[:200]}"
        except Exception as e:
            result.error = repr(e)

        return result

    def extract_from_inventory(
        self,
        inventory_csv_path: Path,
        text_dir: Path,
        output_path: Path | None = None,
        overwrite: bool = False,
    ) -> list[ExtractionResult]:

        # Load already-processed sections so we can skip them on resume
        completed = set()
        if output_path and output_path.exists() and not overwrite:
            with output_path.open() as f:
                for line in f:
                    r = json.loads(line)
                    completed.add((r["source_file"], r["article_number"]))
            print(f"Skipping {len(completed)} already-processed sections")

        inventory = pd.read_csv(inventory_csv_path)
        results = []

        # Append mode — each result written immediately so progress is never lost
        out_fh = output_path.open("a", encoding="utf-8") if output_path else None

        try:
            for _, row in inventory.iterrows():
                labels = (
                    row["labels"].split("|")
                    if pd.notna(row["labels"]) and row["labels"]
                    else []
                )

                if not self.should_process(labels):
                    continue

                key = (row["source_file"], str(row["article_number"]))
                if key in completed:
                    continue

                text_path = text_dir / row["source_file"]
                if not text_path.exists():
                    continue

                full_text = text_path.read_text(encoding="utf-8")
                section_text = full_text[int(row["start_char"]) : int(row["end_char"])]

                print(
                    f"  Extracting: {row['source_file']} | Article {row['article_number']}: {row['article_title']}"
                )

                result = self.extract(
                    source_file=row["source_file"],
                    article_number=str(row["article_number"]),
                    article_title=row["article_title"],
                    labels=labels,
                    text=section_text,
                )

                if result.error:
                    print(f"    ERROR: {result.error}")
                else:
                    print(f"    Found {len(result.timing_rules)} timing rules")

                results.append(result)

                # Write immediately — if the run dies midway, progress is saved
                if out_fh:
                    out_fh.write(json.dumps(result.to_dict()) + "\n")
                    out_fh.flush()

        finally:
            if out_fh:
                out_fh.close()

        return results
