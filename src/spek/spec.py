"""Parse and validate `SPEC.md` using an LLM.

The format is "headers-only": top-level `# Header` sections, each owning the
text up to the next `# Header`. Required sections are `Name`, `Input`,
`Output`, `Implementation`. Any other `# <key>` becomes an entry in
`Spec.extras`.

Unlike a regex parser, this module hands the raw markdown to a small LLM
call whose only job is to return a strict JSON object. The model is
constrained via Anthropic native tool-use with a strict input schema, so
"the LLM returned garbage" is a SpecValidationError rather than a
mysterious downstream crash.

Why an LLM at all? Real users do not write perfectly-formatted markdown:
they capitalise inconsistently, swap `# Outputs` for `# Output`, write
"Inputs:" as a header, or drop a stray `## Sub-section`. The LLM's job
is to be tolerant of those surface variations *while still returning a
well-typed Spec*. The schema constraint keeps the model honest; we
hard-fail on schema violations rather than fall back to anything fuzzy.

Examples
--------
>>> from spek.spec import parse, Spec  # doctest: +SKIP
>>> spec = parse(text, client=fake_client)  # doctest: +SKIP
>>> spec.name  # doctest: +SKIP
'weather-tool'
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from spek.agent.loop import LLMClient


REQUIRED_SECTIONS: tuple[str, ...] = ("Name", "Input", "Output", "Implementation")
DEFAULT_MAX_TOKENS = 2048


class SpecValidationError(ValueError):
    """Raised when a `SPEC.md` is malformed, missing required sections,
    or the LLM produced an unparseable response.
    """


@dataclass(frozen=True)
class Spec:
    """A SPEC.md spec, parsed but not interpreted."""

    name: str
    sections: dict[str, str]
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def input(self) -> str:
        return self.sections["Input"]

    @property
    def output(self) -> str:
        return self.sections["Output"]

    @property
    def implementation(self) -> str:
        return self.sections["Implementation"]


# ---------------------------------------------------------------------------
# LLM tool schema + response model
# ---------------------------------------------------------------------------

# We use Anthropic native tool-use rather than free-form JSON because the
# tool input schema is enforced by the API: the model literally cannot
# return content with the wrong shape (or it will be told off and retry).
SPEC_TOOL_SCHEMA: dict[str, Any] = {
    "name": "emit_spec",
    "description": (
        "Emit the parsed contents of a SPEC.md spec. Call this "
        "exactly once with the four required sections plus any extra "
        "headers. Reproduce section bodies verbatim from the source; do "
        "not summarise or paraphrase."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "First non-empty line of the `# Name` section, "
                    "verbatim from the source. Do not slugify."
                ),
            },
            "sections": {
                "type": "object",
                "description": (
                    "Map of canonical required section name to its body "
                    "text. Keys MUST be exactly: Name, Input, Output, "
                    "Implementation. Body text is the raw markdown of the "
                    "section, with surrounding blank lines stripped."
                ),
                "properties": {
                    "Name": {"type": "string"},
                    "Input": {"type": "string"},
                    "Output": {"type": "string"},
                    "Implementation": {"type": "string"},
                },
                "required": ["Name", "Input", "Output", "Implementation"],
                "additionalProperties": False,
            },
            "extras": {
                "type": "object",
                "description": (
                    "Map of any other top-level `# <key>` headers to their "
                    "body text. Empty object if none. Keys are the header "
                    "text verbatim (without the leading '# ')."
                ),
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["name", "sections", "extras"],
        "additionalProperties": False,
    },
}


class _SpecToolInput(BaseModel):
    """Pydantic mirror of `SPEC_TOOL_SCHEMA` for client-side validation.

    The Anthropic API enforces the schema on its side, but we re-validate
    locally so we get a precise SpecValidationError (with field paths)
    rather than a KeyError three calls deep.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    sections: dict[str, str]
    extras: dict[str, str] = Field(default_factory=dict)


_SYSTEM_PROMPT = """\
You parse `SPEC.md` files for the spek coding agent.

Your only job is to call the `emit_spec` tool exactly once with the parsed \
contents. Do NOT write any prose, ask any questions, or call any other tool.

Required sections (case-insensitive in the source, but always canonical \
TitleCase in your output):
- Name
- Input
- Output
- Implementation

Rules:
1. Reproduce section bodies VERBATIM from the source markdown, including \
list formatting and inline backticks. Do not summarise.
2. The `name` field is the FIRST NON-EMPTY LINE of the `# Name` section, \
verbatim. Do NOT slugify it. The caller will slugify.
3. Tolerate surface-level variations: header capitalisation, trailing \
colons (`# Input:`), pluralisation (`# Outputs` -> Output), and extra \
whitespace. Map them to the four canonical keys.
4. Any top-level `# <key>` header that is NOT one of the four required \
sections goes into `extras`, keyed by the header text WITHOUT the leading \
`# `. Sub-headers (`## ...`) belong to the section they appear under and \
must NOT become extras.
5. If a required section is genuinely missing from the source, OMIT it \
from the `sections` field. The Anthropic schema validator will reject \
that and the caller will produce a precise error message; do NOT \
fabricate content to satisfy the schema.
"""


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug or "package"


def _extract_tool_use(content: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the input of the first `emit_spec` tool_use block, or raise."""
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == "emit_spec":
            inp = block.get("input")
            if isinstance(inp, dict):
                return inp
            raise SpecValidationError("LLM returned an `emit_spec` tool call with non-object input")
    raise SpecValidationError(
        "LLM did not call the `emit_spec` tool; got blocks: "
        + json.dumps([b.get("type") for b in content])
    )


def _build_default_client() -> LLMClient:
    """Lazily construct the production Anthropic client.

    Imported here (rather than at module top) so unit tests that pass a
    fake client never trigger the `anthropic` SDK import path.
    """
    from spek.agent.loop import DEFAULT_MODEL, AnthropicLLMClient

    return AnthropicLLMClient(model=DEFAULT_MODEL)


def parse(
    text: str,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> Spec:
    """Parse SPEC.md text into a `Spec` via an LLM.

    Parameters
    ----------
    text:
        The raw markdown of a `SPEC.md` file.
    client:
        An `LLMClient` (see `spek.agent.loop.LLMClient`). Tests pass a
        `FakeAnthropicClient`. Defaults to a real Anthropic client built
        from environment credentials when `None`.
    model:
        Model slug to use. Defaults to the same default as the agent loop.

    Raises
    ------
    SpecValidationError
        If the input is empty, the LLM call fails, the LLM does not call
        the `emit_spec` tool, the response fails schema validation, or a
        required section is missing or empty.
    """
    if not text.strip():
        raise SpecValidationError("SPEC.md is empty")

    llm = client if client is not None else _build_default_client()
    chosen_model = model or _default_model()

    try:
        resp = llm.create(
            model=chosen_model,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
            tools=[SPEC_TOOL_SCHEMA],
            max_tokens=DEFAULT_MAX_TOKENS,
        )
    except Exception as exc:  # pragma: no cover - depends on transport
        raise SpecValidationError(f"LLM call failed while parsing spec: {exc}") from exc

    raw = _extract_tool_use(resp.content)

    try:
        parsed = _SpecToolInput.model_validate(raw)
    except ValidationError as exc:
        raise SpecValidationError(f"LLM response did not match the spec schema:\n{exc}") from exc

    missing = [s for s in REQUIRED_SECTIONS if s not in parsed.sections]
    if missing:
        joined = ", ".join("# " + m for m in missing)
        raise SpecValidationError(f"SPEC.md is missing required section(s): {joined}")

    name_first_line = parsed.name.strip().splitlines()[0].strip() if parsed.name.strip() else ""
    if not name_first_line:
        raise SpecValidationError(
            "'# Name' section is empty; provide a package name on its first line"
        )

    return Spec(
        name=_slugify(name_first_line),
        sections=dict(parsed.sections),
        extras=dict(parsed.extras),
    )


def parse_file(
    path: str | Path,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> Spec:
    """Parse `SPEC.md` at `path`."""
    p = Path(path)
    return parse(p.read_text(encoding="utf-8"), client=client, model=model)


def _default_model() -> str:
    """Lazy import for the default model so tests don't need the agent loop module
    when passing a fake client (they will, indirectly, but this keeps the dependency
    path explicit).
    """
    from spek.agent.loop import DEFAULT_MODEL

    return DEFAULT_MODEL
