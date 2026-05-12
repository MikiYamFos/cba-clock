from __future__ import annotations

import pytest
from tests.judge import assert_criteria
from tests.test_agent import run_agent_test
from tests.utils import collect_tools


@pytest.mark.asyncio
async def test_searches_before_computing(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the HUD AFGE contract. "
        "What is my Step 2 deadline?",
    )
    await assert_criteria(
        result,
        [
            "calls search_sections or get_timing_rules before calling compute_deadline",
            "cites a specific article number from the contract",
            "explicitly states what holiday schedule was or was not applied and why",
        ],
    )


@pytest.mark.asyncio
async def test_does_not_assume_federal_holidays(agent):
    result = await run_agent_test(
        agent,
        "I need a deadline for a grievance filed May 1st 2026 "
        "under the Verizon Delaware contract. What is my Step 1 deadline?",
    )
    await assert_criteria(
        result,
        [
            "does not assume federal holidays apply",
            "only references holidays explicitly defined in the contract, or states none were applied",
        ],
    )


@pytest.mark.asyncio
async def test_flags_ambiguous_days(agent):
    # First turn — rep gives situation without specifying contract
    result1 = await run_agent_test(
        agent,
        "My contract says I have 5 days to file a grievance after an occurrence. "
        "The occurrence was May 1st 2026. What is my deadline?",
    )
    # Second turn — rep identifies their contract
    result2 = await agent.run(
        "I am covered by the HUD AFGE contract.",
        message_history=result1.new_messages(),
    )
    await assert_criteria(
        result2,
        [
            "checks the contract's definitions article before drawing any conclusion "
            "about what 'days' means",
            "either resolves the ambiguity by citing the contract's definition of 'days', "
            "OR flags it as ambiguous and asks for clarification if no definition is found — "
            "never guesses",
            "does not assume working days means Monday through Friday",
        ],
    )


@pytest.mark.asyncio
async def test_off_topic(agent):
    result = await run_agent_test(agent, "What is a Sicilian defense in chess?")
    await assert_criteria(
        result,
        [
            "declines to answer and explains it only assists with grievance deadlines",
            "makes 0 or 1 tool calls total",
        ],
    )


@pytest.mark.asyncio
async def test_identifies_contract_before_answering(agent):
    result = await run_agent_test(
        agent, "I filed a grievance on May 1st 2026. What is my Step 2 deadline?"
    )
    await assert_criteria(
        result,
        [
            "calls list_contracts or asks the rep which contract applies before computing any deadline",
            "does not assume or guess which contract to use",
        ],
    )


@pytest.mark.asyncio
async def test_does_not_ask_for_information_already_given(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the HUD AFGE contract. "
        "What is my Step 2 deadline?",
    )
    await assert_criteria(
        result,
        [
            "does not ask the rep for the filing date — it was already provided as May 1st 2026",
            "does not ask what step — the rep already said Step 2",
            "does not ask the rep to provide contract language — the agent must use its tools to find it",
        ],
    )


@pytest.mark.asyncio
async def test_uses_tools_to_find_contract_language(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the HUD AFGE contract. "
        "What is my Step 2 deadline?",
    )
    tool_calls = collect_tools(result.new_messages())
    await assert_criteria(
        result,
        [
            "calls search_sections to find the grievance procedure article rather than asking the rep for it",
            "never asks the rep to paste or provide contract text",
        ],
    )


@pytest.mark.asyncio
async def test_understands_numeric_contract_selection(agent):
    result = await run_agent_test(
        agent, "I filed a grievance on May 1st 2026. What is my Step 2 deadline?"
    )
    # First turn — agent should list contracts
    # Second turn — rep selects by number
    result2 = await agent.run("3", message_history=result.new_messages())
    await assert_criteria(
        result2,
        [
            "understands '3' as selecting the third contract from the list it just provided",
            "does not ask the rep to clarify what '3' means",
            "proceeds to look up the Step 2 deadline for the selected contract",
        ],
    )


@pytest.mark.asyncio
async def test_asks_for_work_schedule_when_missing(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the Verizon Delaware contract. "
        "The contract says I have 10 working days to file at Step 2. What is my deadline?",
    )
    await assert_criteria(
        result,
        [
            "asks the rep which days of the week their bargaining unit is scheduled to work "
            "before computing the deadline, because the contract may not operate on a Mon-Fri schedule",
            "does not assume the work schedule is Monday through Friday",
            "does not compute a deadline without first confirming the work schedule",
        ],
    )


@pytest.mark.asyncio
async def test_computes_correctly_for_7_day_operation(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the Verizon Delaware contract. "
        "The contract says I have 10 working days to file at Step 2. "
        "Our bargaining unit works 7 days a week including weekends.",
    )
    await assert_criteria(
        result,
        [
            "uses a 7-day work schedule (Monday through Sunday) when computing the deadline",
            "does not skip weekends when counting working days",
            "explicitly states that the work schedule used was 7 days a week based on what the rep provided",
        ],
    )
