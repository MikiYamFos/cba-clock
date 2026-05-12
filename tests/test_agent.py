from __future__ import annotations

import pytest
from tests.utils import collect_tools


async def run_agent_test(agent, user_prompt):
    result = await agent.run(user_prompt)
    return result


@pytest.mark.asyncio
async def test_agent_runs(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the DOE AFGE 788 contract. What is my Step 2 deadline?",
    )
    assert result.output is not None
    assert len(result.output) > 0


@pytest.mark.asyncio
async def test_agent_uses_tools(agent):
    result = await run_agent_test(
        agent,
        "I filed a grievance on May 1st 2026 under the DOE AFGE 788 contract. What is my Step 2 deadline?",
    )
    tool_calls = collect_tools(result.new_messages())
    assert len(tool_calls) >= 2


@pytest.mark.asyncio
async def test_off_topic_uses_no_tools(agent):
    result = await run_agent_test(agent, "What is a Sicilian defense in chess?")
    tool_calls = collect_tools(result.new_messages())
    assert len(tool_calls) <= 1
