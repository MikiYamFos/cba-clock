"""
worker/app/agent.py

Grievance deadline agent. Uses Claude tool use to:
  1. Search contract sections (Elasticsearch)
  2. Get extracted timing rules (JSONL)
  3. Compute deadlines (compute_deadline.py)
  4. List available contracts (Elasticsearch)

Usage:
    python -m worker.app.agent
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.anthropic import AnthropicModel

from worker.app.es_search import CBASectionSearcher
from worker.app.compute_deadline import describe_deadline

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/opt/cba_clock"))
DEADLINES_OUTPUT = PROJECT_ROOT / "data" / "processed" / "deadline_extractions.jsonl"
SYSTEM_PROMPT = """You are a grievance deadline assistant for union representatives.

You have tools to:
- List available contracts
- Search contract language by keyword or phrase
- Get structured timing rules already extracted from a contract article
- Get stipulation rules (eligibility, procedural requirements) from a contract article
- Compute exact deadline dates from a trigger date and a timing rule

When a rep gives you a situation:
1. Identify the right contract using list_contracts if needed. If the rep selects
   a contract by number from a list you provided, use that number as the index —
   do not ask them to clarify what the number means.
2. Before computing ANY deadline, search the contract for work schedule and holiday
   definitions. Search for terms like 'work week', 'working day', 'scheduled days',
   'business day', 'holiday', 'days defined' in the definitions, recognition, or
   scheduling articles.
   - work_schedule: which days of the week this bargaining unit works. A school
     cafeteria works Mon-Fri. A casino works 7 days. An airbase cafeteria works
     7 days. Never assume — find it in the contract.
   - holiday_schedule: which specific dates are non-working. For federal employee
     unions and public school employee unions, federal holidays apply automatically
     unless the contract says otherwise. For all other contracts, never assume any
     holiday schedule — only apply holidays the contract explicitly defines.
   - If unit is working_days or business_days and you cannot find the work schedule
     definition in the contract, ask the rep: "I need to know which days of the week
     your bargaining unit is scheduled to work before I can compute this deadline.
     Does your contract define the work week?" Do not compute without this.
3. If the rep's message contains the word 'days' without specifying working or
   calendar, immediately flag this BEFORE asking which contract or doing anything
   else: "Note: your message uses the word 'days' without specifying working or
   calendar days. This is ambiguous and affects your deadline significantly. I will
   check the contract's definitions article for clarification." Then proceed.
4. Never re-question a date the rep already provided. If the rep says "I filed on
   May 1st", treat May 1st as the trigger date and proceed. Do not ask whether it
   was a Step 1 filing date or Step 2 advancement date — assume it is the trigger
   for whatever step they asked about.
5. Search for the relevant articles (grievance procedure, arbitration, etc.)
6. Get the timing rules for those articles using get_timing_rules. Also call
   get_stipulation_rules to understand any eligibility or procedural requirements
   that must be met before a deadline applies.
7. Compute each deadline, passing holiday_schedule only if the contract explicitly
   defines which holidays are non-working days.
8. Explain in plain English what must happen by when, who must act, and what the
   consequence of missing the deadline is based on the contract language.

When tools return empty or incomplete results:
- Try alternative search terms before giving up. If 'grievance procedure' returns
  nothing, try 'grievance', 'step 1', 'step 2', 'filing deadline'.
- If get_timing_rules returns empty, try searching for the article directly using
  search_sections with the article number or title.
- Never ask the rep to provide or paste contract language under any circumstances.
  Not even as a last resort. If after exhausting search options you still cannot
  find the rule, tell the rep exactly what you searched for and what was not found,
  then stop. Do not ask them to look it up.
- Always end with an explicit holiday schedule statement in this exact form:
  "No holiday schedule was applied because [reason]." or
  "The following holiday schedule was applied: [dates], because the contract
  defines these as non-working days in [article]."
  Never leave the holiday schedule decision implicit.

Always tell the rep which article and which exact contract language each deadline
comes from. If contract language varies across articles, flag that conflict
explicitly."""

agent = Agent(
    model=AnthropicModel("claude-sonnet-4-6"),
    system_prompt=SYSTEM_PROMPT,
)
searcher = CBASectionSearcher()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


@agent.tool
def list_contracts(ctx: RunContext[None]) -> dict:
    """List all contracts currently indexed and searchable, with human-readable
    employer and union names. Use this when the rep hasn't specified which contract,
    or to confirm a contract name. Match the rep's natural language description
    (e.g. 'HUD AFGE') to the display_name field."""
    try:
        response = searcher.es.search(
            index="cba_sections",
            body={
                "size": 0,
                "aggs": {"contracts": {"terms": {"field": "source_file", "size": 100}}},
            },
        )
        filenames = [
            bucket["key"] for bucket in response["aggregations"]["contracts"]["buckets"]
        ]

        from worker.app.metadata_lookup import lookup

        contracts = []
        for filename in filenames:
            meta = lookup(filename)
            if meta:
                parts = [
                    p
                    for p in [meta.employer_name, meta.union_name, meta.union_local]
                    if p
                ]
                display_name = " – ".join(parts) if parts else filename
            else:
                display_name = filename
            contracts.append(
                {
                    "filename": filename,
                    "display_name": display_name,
                }
            )

        return {"contracts": contracts, "count": len(contracts)}
    except Exception as e:
        return {"error": str(e)}


@agent.tool
def search_sections(
    ctx: RunContext[None],
    query: str,
    source_file: str | None = None,
    phrase_match: bool = False,
) -> dict:
    """Search contract sections by keyword or phrase. Returns matching sections with highlighted fragments. Use phrase_match=True for exact contract language like '10 working days'."""
    try:
        if source_file:
            result = searcher.search_within_cba(
                query=query, source_file=source_file, phrase_match=phrase_match, size=5
            )
        else:
            result = searcher.search_cross_cba(
                query=query, phrase_match=phrase_match, size=5
            )
        return {
            "total_hits": result.total_hits,
            "hits": [
                {
                    "source_file": h.source_file,
                    "article_number": h.article_number,
                    "article_title": h.article_title,
                    "labels": h.labels,
                    "highlights": [f.fragment for f in h.highlights],
                    "score": h.score,
                }
                for h in result.hits
            ],
        }
    except Exception as e:
        return {"error": str(e)}


TIMING_RULE_TYPES = {"deadline_rule", "window_rule", "grievance_trigger"}


@agent.tool
def get_timing_rules(
    ctx: RunContext[None],
    source_file: str,
    article_number: str | None = None,
) -> dict:
    """Get deadline-computable rules from a contract article. Returns deadline_rule,
    window_rule, grievance_trigger, and ambiguous rules that have an offset_days value.
    Use this before calling compute_deadline."""
    if not DEADLINES_OUTPUT.exists():
        return {"error": f"No extractions found at {DEADLINES_OUTPUT}"}

    results = []
    with DEADLINES_OUTPUT.open() as f:
        for line in f:
            record = json.loads(line)
            if record["source_file"] != source_file:
                continue
            if article_number and str(record["article_number"]) != str(article_number):
                continue

            timing_rules = [
                r
                for r in record.get("rules", [])
                if r.get("rule_type") in TIMING_RULE_TYPES
                or (
                    r.get("rule_type") == "ambiguous_rule"
                    and r.get("offset_days") is not None
                )
            ]

            if timing_rules:
                results.append(
                    {
                        "article_number": record["article_number"],
                        "article_title": record["article_title"],
                        "rules": timing_rules,
                        "notes": record.get("notes", ""),
                    }
                )

    return {"source_file": source_file, "results": results, "count": len(results)}


STIPULATION_RULE_TYPES = {
    "eligibility_or_selection_rule",
    "procedural_requirement",
    "other",
}


@agent.tool
def get_stipulation_rules(
    ctx: RunContext[None],
    source_file: str,
    article_number: str | None = None,
) -> dict:
    """Get non-deadline rules from a contract article — eligibility requirements,
    procedural requirements, and ambiguous rules without a computable offset.
    Use this to understand prerequisites or conditions before computing a deadline."""
    if not DEADLINES_OUTPUT.exists():
        return {"error": f"No extractions found at {DEADLINES_OUTPUT}"}

    results = []
    with DEADLINES_OUTPUT.open() as f:
        for line in f:
            record = json.loads(line)
            if record["source_file"] != source_file:
                continue
            if article_number and str(record["article_number"]) != str(article_number):
                continue

            stipulation_rules = [
                r
                for r in record.get("rules", [])
                if r.get("rule_type") in STIPULATION_RULE_TYPES
                or (
                    r.get("rule_type") == "ambiguous_rule"
                    and r.get("offset_days") is None
                )
            ]

            if stipulation_rules:
                results.append(
                    {
                        "article_number": record["article_number"],
                        "article_title": record["article_title"],
                        "rules": stipulation_rules,
                        "notes": record.get("notes", ""),
                    }
                )

    return {"source_file": source_file, "results": results, "count": len(results)}


@agent.tool
def compute_deadline(
    ctx: RunContext[None],
    trigger_date: str,
    offset: int,
    unit: str,
    action: str,
    party: str,
    holiday_schedule: list[str] | None = None,
    work_schedule: list[int] | None = None,
) -> dict:
    """Compute an exact deadline date from a trigger date, offset, and unit.

    work_schedule is required for working_days and business_days — pass a list
    of integers representing which days this bargaining unit works
    (0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday).
    Example: Mon-Fri = [0,1,2,3,4], 7-day operation = [0,1,2,3,4,5,6].
    Never assume Mon-Fri — get this from the contract's definitions article first.

    Only pass holiday_schedule if the contract explicitly defines those days as
    non-working. Do not include holidays where employees work and receive premium
    pay — those are still working days for deadline purposes."""
    try:
        return describe_deadline(
            trigger_date=trigger_date,
            offset_days=offset,
            unit=unit,
            action=action,
            party=party,
            holiday_schedule=holiday_schedule,
            work_schedule=work_schedule,
        )
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def main():
        print("CBA Clock Grievance Deadline Agent")
        print("Type your question or 'quit' to exit.\n")
        while True:
            user_input = input("Rep: ").strip()
            if user_input.lower() in ("quit", "exit"):
                break
            if not user_input:
                continue
            print("\nAgent: thinking...\n")
            result = await agent.run(user_input)
            print(f"Agent: {result.output}\n")

    asyncio.run(main())
