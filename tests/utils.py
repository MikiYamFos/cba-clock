from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolCall:
    tool_name: str
    args: dict

    def __str__(self):
        return f"{self.tool_name}({self.args})"


def collect_tools(messages: list) -> list[ToolCall]:
    tool_calls = []
    for message in messages:
        for part in message.parts:
            if part.part_kind == "tool-call":
                tool_calls.append(ToolCall(tool_name=part.tool_name, args=part.args))
    return tool_calls
