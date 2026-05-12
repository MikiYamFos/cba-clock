from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from tests.utils import collect_tools

judge_instructions = """

You are an expert evaluator for AI contract-rule extraction.
Evaluate whether the agent output satisfies each criterion.
Be strict. Only mark a criterion as passed when there is clear evidence in the output.
Do not give credit for vague or implied behavior if the required field/value is missing.

""".strip()

judge_user_prompt_template = """
Evaluate the agent's performance based on the following criteria:
<CRITERIA>
{criteria}
</CRITERIA>

The agent's final output was:
<AGENT_OUTPUT>
{output}
</AGENT_OUTPUT>

Tool calls:
<TOOL_CALLS>
{tool_calls}
</TOOL_CALLS>
""".strip()


class JudgeCriterion(BaseModel):
    criterion_description: str = Field(
        description="The specific requirement or rule being evaluated."
    )
    passed: bool = Field(description="Whether the agent satisfied this requirement.")
    judgement: str = Field(
        description="Clear explanation of why the agent passed or failed, referencing specific evidence."
    )


class JudgeFeedback(BaseModel):
    criteria: list[JudgeCriterion] = Field(
        description="Individual evaluations for each performance requirement."
    )
    feedback: str = Field(
        description="Holistic summary of the agent's overall performance."
    )


def create_judge_agent():
    return Agent(
        name="judge",
        model="anthropic:claude-haiku-4-5-20251001",
        instructions=judge_instructions,
        output_type=JudgeFeedback,
    )


async def assert_criteria(result, criteria: list[str]) -> None:
    messages = result.new_messages()
    tool_calls = collect_tools(messages)
    output = str(result.output)

    judge_agent = create_judge_agent()
    judge_user_prompt = judge_user_prompt_template.format(
        criteria="\n".join(criteria),
        output=output,
        tool_calls="\n".join([str(tc) for tc in tool_calls]),
    )

    judge_result = await judge_agent.run(judge_user_prompt)

    print("judge feedback:")
    print(judge_result.output.feedback)

    for criterion in judge_result.output.criteria:
        print(f"{criterion.criterion_description}: {criterion.judgement}")
        assert (
            criterion.passed
        ), f"{criterion.criterion_description}: {criterion.judgement}"
