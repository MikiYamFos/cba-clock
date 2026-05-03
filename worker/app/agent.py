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

import anthropic

from worker.app.es_search import CBASectionSearcher
from worker.app.compute_deadline import describe_deadline

DEADLINES_OUTPUT = Path("/opt/cba_clock/data/processed/deadline_extractions.jsonl")

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
searcher = CBASectionSearcher()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a grievance deadline assistant for union representatives.

You have tools to:
- List available contracts
- Search contract language by keyword or phrase
- Get structured timing rules already extracted from a contract article
- Compute exact deadline dates from a trigger date and a timing rule

When a rep gives you a situation:
1. Identify the right contract using list_contracts if needed
2. Before computing ANY deadline, search the contract for holiday and working day definitions.
   Search for terms like 'holiday', 'working day', 'business day', 'days defined' in the
   definitions or recognition articles. Do not assume federal holidays apply — this is a
   private sector contract unless you have confirmed otherwise. The contract's own language
   is the only authority on what counts as a non-working day.
3. Search for the relevant articles (grievance procedure, arbitration, etc.)
4. Get the timing rules for those articles
5. Compute each deadline, passing holiday_schedule only if the contract explicitly defines
   which holidays are non-working days. If the contract defines no holidays, pass no
   holiday_schedule — working_days means Mon-Fri only.
6. Explain in plain English what must happen by when, who must act, and what the consequence
   of missing the deadline is based on the contract language

Always tell the rep which article and which exact contract language each deadline comes from.
Always tell the rep what holiday schedule was or was not applied and why.
If a rule is ambiguous — for example the contract says 'days' without specifying working or
calendar — say so explicitly and ask the rep to clarify before computing.
If contract language varies across articles (e.g. Article 3 defines working days differently
than Article 12 assumes), flag that conflict explicitly."""


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_contracts",
        "description": "List all contracts currently indexed and searchable. Use this when the rep hasn't specified which contract, or to confirm a contract name.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "search_sections",
        "description": (
            "Search contract sections by keyword or phrase. Returns matching sections with "
            "highlighted fragments showing where the match appears. "
            "Use phrase_match=true for exact contract language like '10 working days'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'grievance procedure' or '10 working days'",
                },
                "source_file": {
                    "type": "string",
                    "description": "Scope search to one contract file. Omit to search all contracts.",
                },
                "phrase_match": {
                    "type": "boolean",
                    "description": "If true, match the exact phrase. If false, fuzzy keyword search.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_timing_rules",
        "description": (
            "Get structured timing rules already extracted by Claude from a specific contract article. "
            "Returns rules with action, trigger, offset, unit, party, and source quote."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_file": {
                    "type": "string",
                    "description": "Contract filename, e.g. 'usps_2023.txt'",
                },
                "article_number": {
                    "type": "string",
                    "description": "Article number, e.g. '12'",
                },
            },
            "required": ["source_file"],
        },
    },
    {
        "name": "compute_deadline",
        "description": (
            "Compute an exact deadline date from a trigger date, offset, and unit. "
            "For working_days, skips weekends always. Skips contract-defined holidays "
            "only if you pass them explicitly via holiday_schedule — do NOT pass holidays "
            "unless the contract language defines them as non-working days. "
            "Returns the deadline date, day of week, and a plain-English description "
            "including what holiday schedule was applied."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trigger_date": {
                    "type": "string",
                    "description": "ISO date when the clock started, e.g. '2026-05-01'",
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of units, e.g. 10",
                },
                "unit": {
                    "type": "string",
                    "description": "One of: calendar_days, working_days, business_days, weeks, months",
                },
                "action": {
                    "type": "string",
                    "description": "What must happen, e.g. 'file grievance at Step 1'",
                },
                "party": {
                    "type": "string",
                    "description": "Who must act, e.g. 'employee' or 'employer'",
                },
                "holiday_schedule": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "ISO date strings of non-working holidays under THIS contract, "
                        "e.g. ['2026-11-26', '2026-12-25']. Only pass this if the contract "
                        "explicitly defines these days as non-working. Most private sector "
                        "contracts either define no holidays or define a specific list. "
                        "Never assume federal holidays apply."
                    ),
                },
            },
            "required": ["trigger_date", "offset", "unit", "action", "party"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def list_contracts() -> dict:
    try:
        response = searcher.es.search(
            index="cba_sections",
            body={
                "size": 0,
                "aggs": {"contracts": {"terms": {"field": "source_file", "size": 100}}},
            },
        )
        contracts = [
            bucket["key"] for bucket in response["aggregations"]["contracts"]["buckets"]
        ]
        return {"contracts": contracts, "count": len(contracts)}
    except Exception as e:
        return {"error": str(e)}


def search_sections(
    query: str, source_file: str | None = None, phrase_match: bool = False
) -> dict:
    try:
        if source_file:
            result = searcher.search_within_cba(
                query=query,
                source_file=source_file,
                phrase_match=phrase_match,
                size=5,
            )
        else:
            result = searcher.search_cross_cba(
                query=query,
                phrase_match=phrase_match,
                size=5,
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


def get_timing_rules(source_file: str, article_number: str | None = None) -> dict:
    if not DEADLINES_OUTPUT.exists():
        return {"error": f"No extractions found at {DEADLINES_OUTPUT}"}

    rules = []
    with DEADLINES_OUTPUT.open() as f:
        for line in f:
            record = json.loads(line)
            if record["source_file"] != source_file:
                continue
            if article_number and str(record["article_number"]) != str(article_number):
                continue
            if record.get("timing_rules"):
                rules.append(
                    {
                        "article_number": record["article_number"],
                        "article_title": record["article_title"],
                        "timing_rules": record["timing_rules"],
                        "notes": record.get("notes", ""),
                    }
                )

    return {"source_file": source_file, "results": rules, "count": len(rules)}


def run_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "list_contracts":
        result = list_contracts()
    elif tool_name == "search_sections":
        result = search_sections(**tool_input)
    elif tool_name == "get_timing_rules":
        result = get_timing_rules(**tool_input)
    elif tool_name == "compute_deadline":
        try:
            result = describe_deadline(**tool_input)
        except Exception as e:
            result = {"error": str(e)}
    else:
        result = {"error": f"Unknown tool: {tool_name}"}

    return json.dumps(result)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def run_agent(user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Add assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        # If Claude is done, return the final text
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text

        # If Claude wants to use tools, run them and feed results back
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [tool call] {block.name}({json.dumps(block.input)})")
                    output = run_tool(block.name, block.input)
                    print(f"  [tool result] {output[:200]}...")
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("CBA Clock Grievance Deadline Agent")
    print("Type your question or 'quit' to exit.\n")

    while True:
        user_input = input("Rep: ").strip()
        if user_input.lower() in ("quit", "exit"):
            break
        if not user_input:
            continue

        print("\nAgent: thinking...\n")
        answer = run_agent(user_input)
        print(f"Agent: {answer}\n")
