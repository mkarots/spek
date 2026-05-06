"""`epistemic` tool: pause and ask the user.

The agent uses this whenever it is missing information that only the
human can supply. The tool prints the question, blocks on the configured
stdin, and returns the user's free-text answer back to the model.

Streams come from `ToolContext` so tests can substitute in-memory buffers
and verify the prompt format.
"""

from __future__ import annotations

from typing import IO, Any

from spek.tools.base import Tool, ToolContext, ToolError

_SCHEMA: dict[str, Any] = {
    "name": "epistemic",
    "description": (
        "Pause and ask the human user a clarifying question. Use this whenever "
        "you need information that is not in the spec, e.g. an undefined "
        "default, an ambiguous behaviour, or a choice between framework "
        "options. The user's free-text answer is returned to you verbatim."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
            "why_blocked": {
                "type": "string",
                "description": "Why you cannot proceed without an answer.",
            },
            "how_to_resolve": {
                "type": "string",
                "description": "What kind of answer would unblock you.",
            },
        },
        "required": ["question", "why_blocked", "how_to_resolve"],
    },
}


def _format_prompt(question: str, why_blocked: str, how_to_resolve: str) -> str:
    return (
        "\n--- spek needs input ---------------------------------------\n"
        f"Question      : {question}\n"
        f"Why blocked   : {why_blocked}\n"
        f"How to resolve: {how_to_resolve}\n"
        "Type your answer and press Enter (Ctrl-D to send a multi-line answer ending with EOF).\n"
        "> "
    )


def _read_answer(stdin: IO[str]) -> str:
    """Read a single line from `stdin`; if empty, fall back to read-until-EOF."""
    line: str = stdin.readline()
    if not line:
        return ""
    line = line.rstrip("\n")
    if line:
        return line
    # Empty first line: treat as "use multi-line mode".
    rest: str = stdin.read()
    return rest.rstrip("\n")


def _run(input: dict[str, Any], ctx: ToolContext) -> str:
    question = input.get("question")
    why_blocked = input.get("why_blocked", "")
    how_to_resolve = input.get("how_to_resolve", "")
    if not isinstance(question, str) or not question.strip():
        raise ToolError("epistemic: 'question' is required and must be a non-empty string")

    ctx.stdout.write(_format_prompt(question, str(why_blocked), str(how_to_resolve)))
    ctx.stdout.flush()
    answer = _read_answer(ctx.stdin)
    if not answer.strip():
        return "(no answer provided; user pressed Enter without typing anything)"
    return answer


EPISTEMIC_TOOL = Tool(name="epistemic", schema=_SCHEMA, run=_run)
