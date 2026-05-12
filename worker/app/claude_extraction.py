from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent

load_dotenv()

MODEL = "claude-sonnet-4-6"


class ExtractedRule(BaseModel):
    rule_type: Literal[
        "deadline_rule",
        "window_rule",
        "grievance_trigger",
        "eligibility_or_selection_rule",
        "procedural_requirement",
        "ambiguous_rule",
        "other",
    ] = Field(description="The type of operational contract rule.")
    domain_area: Literal[
        "dispute_resolution",
        "discipline_adverse_actions",
        "performance_management",
        "staffing_career_movement",
        "training_development",
        "leave_attendance",
        "work_schedule_overtime",
        "union_structure_representation",
        "bargaining_contract_term",
        "pay_awards_benefits",
        "health_safety_accommodation",
        "facilities_equipment_workplace",
        "records_privacy_admin",
        "management_rights_operations",
        "legal_compliance",
        "other",
    ] = Field(description="The contract domain area.")
    action_required: str | None = None
    trigger_event: str | None = None
    offset_days: int | None = None
    unit: (
        Literal[
            "calendar_days", "working_days", "business_days", "weeks", "months", "hours"
        ]
        | None
    ) = None
    direction: Literal["before", "after", "within", "none"] = "none"
    anchor_date: str | None = None
    window_start_offset: int | None = None
    window_end_offset: int | None = None
    work_schedule: list[int] | None = Field(
        default=None,
        description=(
            "Which days of the week this bargaining unit works, as a list of integers "
            "(0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday). "
            "Extract this from the contract's definitions or recognition article. "
            "A school cafeteria on Mon-Fri = [0,1,2,3,4]. A casino operating 7 days = [0,1,2,3,4,5,6]. "
            "A hospital with rotating shifts would define this in the scheduling article. "
            "Set to None if the contract does not define which days are working days — "
            "do not assume Mon-Fri."
        ),
    )
    has_deadline: bool = False
    requires_user_input: bool = False
    needs_review: bool = False
    source_text: str


class ExtractionOutput(BaseModel):
    rules: list[ExtractedRule] = Field(
        default_factory=list,
        description="All rules found in this section. Empty list if none found.",
    )
    notes: str | None = Field(
        default=None,
        description="Overall notes about this section — conflicts, unusual patterns, etc.",
    )


extraction_agent = Agent(
    f"anthropic:{MODEL}",
    output_type=ExtractionOutput,
    instructions="""You extract structured operational rules from collective bargaining agreement language.
Return only structured data matching the output schema.

--- RULE TYPE DECISIONS ---
Use these definitions to assign rule_type:

- deadline_rule: a clause with a specific numeric offset and unit that creates a hard
  deadline. Must have both a trigger event and an offset (e.g. "within 10 working days
  of receipt"). Set has_deadline=true.

- window_rule: a clause that defines a time window with a start and end offset, not a
  single deadline (e.g. "between 5 and 15 days after occurrence"). Set has_deadline=true,
  populate window_start_offset and window_end_offset.

- grievance_trigger: a clause that defines what event or condition initiates a grievance
  clock, without itself specifying a deadline (e.g. "a grievance must be filed after the
  occurrence or when the employee knew or should have known").

- eligibility_or_selection_rule: a clause about who qualifies for something or how
  someone is selected (e.g. "seniority shall govern", "employees with 6 months service
  are eligible"). No deadline.

- procedural_requirement: a clause that requires a specific step or action to occur
  before or during a process, without a numeric deadline (e.g. "the union must be
  present at all Step 2 meetings", "grievances must be filed in writing").

- ambiguous_rule: a clause that appears to create a deadline or obligation but is
  unclear. Use when:
    - the contract says only "days" without specifying working or calendar
    - the trigger is undefined or vague ("promptly", "reasonable time", "as soon as
      possible")
    - the clause could be interpreted as either a deadline or a procedural requirement
  Always set needs_review=true for ambiguous_rule.

- other: enforceable rights or obligations that don't fit the above categories.

--- FIELD RULES ---
- Do not invent dates or numeric deadlines not present in the text.
- If the clause has a trigger event but no trigger date is given, set requires_user_input=true.
- If language is vague ("promptly", "reasonable time"), set needs_review=true.
- Preserve the exact source text in source_text — do not paraphrase.
- Never assume working_days or business_days means Mon-Fri. Many private sector
  employers (casinos, restaurants, retail, hospitals) operate 7 days a week.
  Some contracts cap weekly hours or define overtime rules for excess hours during
  special events or short-staffing — these affect what counts as a working day.
  The definition of a working day is whatever the contract explicitly defines.
  If the contract does not define working days, set needs_review=true.
- calendar_days counts every day including weekends — use this only when the
  contract explicitly says "calendar days".
- For federal employee unions and public school employee unions, federal holidays
  apply automatically as non-working days unless the contract says otherwise.
  For all other private sector contracts, never assume federal holidays are
  non-working days — only apply holidays the contract explicitly defines.
- If the contract says only "days" without specifying working or calendar, set
  rule_type="ambiguous_rule" and needs_review=true.
- For work_schedule: look for language in definitions, recognition, or scheduling
  articles that defines which days the bargaining unit works. Examples:
    "The work week shall be Monday through Friday" → [0,1,2,3,4]
    "Employees may be scheduled any day of the week" → [0,1,2,3,4,5,6]
    "The standard work week consists of five 8-hour days" with no days specified → None
  If the contract does not explicitly define working days, set work_schedule=None.
  Never assume Mon-Fri.
- Set has_deadline=true only for deadline_rule and window_rule.
  """,
)


@dataclass
class ExtractionResult:
    source_file: str
    article_number: str
    article_title: str
    labels: list[str]
    extraction_model: str
    rules: list[dict] = field(default_factory=list)
    notes: str = ""
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "article_number": self.article_number,
            "article_title": self.article_title,
            "labels": self.labels,
            "extraction_model": self.extraction_model,
            "rules": self.rules,
            "notes": self.notes,
            "error": self.error,
        }


USER_PROMPT_TEMPLATE = """Extract all operational rules from this contract section.

Source: {source_file}
Article {article_number}: {article_title}

---
{text}
---"""


class DeadlineExtractor:
    RELEVANT_LABELS = {"grievance_procedure", "arbitration", "contract_term"}

    def __init__(self, max_section_chars: int = 12_000) -> None:
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
            run_result = extraction_agent.run_sync(user_prompt)
            output: ExtractionOutput = run_result.output
            result.rules = [rule.model_dump() for rule in output.rules]
            result.notes = output.notes or ""
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

        completed = set()
        if output_path and output_path.exists() and not overwrite:
            with output_path.open() as f:
                for line in f:
                    r = json.loads(line)
                    completed.add((r["source_file"], r["article_number"]))
            print(f"Skipping {len(completed)} already-processed sections")

        inventory = pd.read_csv(inventory_csv_path)
        results = []
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
                    print(f"    Found {len(result.rules)} rules")

                results.append(result)

                if out_fh:
                    out_fh.write(json.dumps(result.to_dict()) + "\n")
                    out_fh.flush()

        finally:
            if out_fh:
                out_fh.close()

        return results
