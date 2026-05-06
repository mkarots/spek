"""Tests for the LLM-backed spec parser.

The parser delegates the structural work to an LLM constrained by an
Anthropic native tool-use schema. We test both happy paths and the
"the LLM misbehaved" failure modes against a `FakeAnthropicClient` so
tests run without network access.
"""

from __future__ import annotations

import pytest

from spek.agent.loop import LLMResponse
from spek.spec import REQUIRED_SECTIONS, Spec, SpecValidationError, parse
from tests.fakes import FakeAnthropicClient, spec_response, text_response, tool_use_response

GOOD_SPEC = """# Name
Weather Tool

# Input
--city str
--metric str (F|C)

# Output
A line of text with the temperature.

# Implementation
Python with click for the CLI.

# city
The city to query. Defaults to London.
"""


def _client(*responses: LLMResponse) -> FakeAnthropicClient:
    return FakeAnthropicClient(responses=list(responses))


def test_parse_happy_path_extracts_required_sections() -> None:
    client = _client(
        spec_response(
            "Weather Tool",
            input="--city str\n--metric str (F|C)",
            output="A line of text with the temperature.",
            implementation="Python with click for the CLI.",
            extras={"city": "The city to query. Defaults to London."},
        )
    )

    spec = parse(GOOD_SPEC, client=client)

    assert spec.name == "weather-tool"
    assert "--city" in spec.input
    assert "temperature" in spec.output
    assert "Python" in spec.implementation


def test_parse_preserves_extras_returned_by_llm() -> None:
    client = _client(
        spec_response(
            "Weather Tool",
            extras={"city": "The city to query. Defaults to London."},
        )
    )

    spec = parse(GOOD_SPEC, client=client)

    assert "city" in spec.extras
    assert spec.extras["city"].startswith("The city to query")


def test_parse_slugifies_first_line_of_name() -> None:
    client = _client(spec_response("Some Cool Tool!"))
    src = "# Name\nSome Cool Tool!\n# Input\nx\n# Output\ny\n# Implementation\nz\n"

    spec = parse(src, client=client)

    assert spec.name == "some-cool-tool"


def test_parse_uses_only_first_non_empty_line_for_name() -> None:
    """If the LLM returns a multi-line `name` value, we slugify the first line only."""
    client = _client(
        tool_use_response(
            "emit_spec",
            {
                "name": "weather-tool\nA CLI tool",
                "sections": {
                    "Name": "weather-tool",
                    "Input": "x",
                    "Output": "y",
                    "Implementation": "z",
                },
                "extras": {},
            },
        )
    )

    src = "# Name\nweather-tool\n# Input\nx\n# Output\ny\n# Implementation\nz\n"
    spec = parse(src, client=client)

    assert spec.name == "weather-tool"


@pytest.mark.parametrize("missing", REQUIRED_SECTIONS)
def test_parse_raises_when_llm_omits_required_section(missing: str) -> None:
    """If the LLM returns sections without one of the four required keys, fail loudly."""
    sections = {k: "body" for k in REQUIRED_SECTIONS if k != missing}
    client = _client(
        tool_use_response(
            "emit_spec",
            {"name": "x", "sections": sections, "extras": {}},
        )
    )

    with pytest.raises(SpecValidationError) as excinfo:
        parse(GOOD_SPEC, client=client)

    # The Pydantic schema has all four sections as required, so the failure
    # surfaces at validation time with the missing field path.
    assert missing in str(excinfo.value)


def test_parse_raises_when_llm_returns_empty_name() -> None:
    client = _client(
        tool_use_response(
            "emit_spec",
            {
                "name": "   \n   ",
                "sections": {k: "body" for k in REQUIRED_SECTIONS},
                "extras": {},
            },
        )
    )

    with pytest.raises(SpecValidationError, match="Name"):
        parse(GOOD_SPEC, client=client)


def test_parse_raises_when_llm_does_not_call_emit_spec() -> None:
    """The LLM may "go off-script" and emit prose; we hard-fail rather than guess."""
    client = _client(text_response("I have parsed the spec for you. Here is..."))

    with pytest.raises(SpecValidationError, match="did not call"):
        parse(GOOD_SPEC, client=client)


def test_parse_raises_when_tool_input_is_not_an_object() -> None:
    client = _client(
        LLMResponse(
            content=[{"type": "tool_use", "id": "x", "name": "emit_spec", "input": "garbage"}],
            stop_reason="tool_use",
        )
    )

    with pytest.raises(SpecValidationError, match="non-object input"):
        parse(GOOD_SPEC, client=client)


def test_parse_raises_when_extras_has_non_string_values() -> None:
    """Schema violation: extras must be `dict[str, str]`."""
    client = _client(
        tool_use_response(
            "emit_spec",
            {
                "name": "x",
                "sections": {k: "body" for k in REQUIRED_SECTIONS},
                "extras": {"city": 42},  # wrong type
            },
        )
    )

    with pytest.raises(SpecValidationError):
        parse(GOOD_SPEC, client=client)


def test_parse_raises_on_empty_input_without_calling_llm() -> None:
    client = _client()  # no responses scripted

    with pytest.raises(SpecValidationError, match="empty"):
        parse("   \n  \n", client=client)

    # Empty input is rejected before the LLM is consulted.
    assert client.calls == []


def test_parse_raises_when_llm_call_fails() -> None:
    class BoomClient:
        def create(self, **_: object) -> LLMResponse:
            raise RuntimeError("transport down")

    with pytest.raises(SpecValidationError, match="LLM call failed"):
        parse(GOOD_SPEC, client=BoomClient())


def test_parse_passes_full_text_to_llm_as_user_message() -> None:
    client = _client(spec_response("weather"))

    parse(GOOD_SPEC, client=client)

    assert len(client.calls) == 1
    call = client.calls[0]
    user_msgs = [m for m in call["messages"] if m["role"] == "user"]
    assert user_msgs, "spec parser must send the source as a user message"
    assert "Weather Tool" in user_msgs[0]["content"][0]["text"]
    assert call["tools"] == ["emit_spec"]


def test_spec_is_frozen_dataclass() -> None:
    client = _client(spec_response("weather-tool"))
    spec = parse(GOOD_SPEC, client=client)

    with pytest.raises(AttributeError):
        spec.name = "other"  # type: ignore[misc]


def test_spec_property_accessors_match_sections() -> None:
    client = _client(
        spec_response("weather-tool", input="--city", output="temperature", implementation="Python")
    )

    spec: Spec = parse(GOOD_SPEC, client=client)

    assert spec.input == spec.sections["Input"]
    assert spec.output == spec.sections["Output"]
    assert spec.implementation == spec.sections["Implementation"]
