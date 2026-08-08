"""The shared metered plumbing: prompts, schema-first calls, retries, map-reduce."""

from __future__ import annotations

import json

import pytest

from session_ingest import llm
from session_ingest.llm import (
    LlmError,
    PromptMissing,
    SchemaViolation,
    Usage,
    load_prompt,
    map_reduce,
    metered_skip,
    nullable,
    object_schema,
    prompt_filename,
    schema_errors,
    structured_call,
)
from session_ingest.vaultfiles import load_lexicon

from .fakes import FakeClient, constant, make_config, sequence

SIMPLE_SCHEMA = object_schema(
    {
        "items": {
            "type": "array",
            "items": object_schema(
                {"name": {"type": "string"}, "kind": {"type": "string", "enum": ["a", "b"]}}
            ),
        }
    }
)

GOOD = {"items": [{"name": "x", "kind": "a"}]}
BAD = {"items": [{"name": 7, "kind": "z"}]}


# ------------------------------------------------------------ metered skip


def test_metered_skip_returns_none_when_a_key_is_present() -> None:
    assert metered_skip(make_config(), "extract") is None


def test_metered_skip_envelope_is_a_clean_exit() -> None:
    payload = metered_skip(make_config(api_key=None), "extract")
    assert payload is not None
    assert payload["status"] == "skipped"
    assert payload["code"] == "missing_api_key"
    assert payload["verb"] == "extract"
    assert payload["metered"] is True
    assert payload["next_steps"] == []


def test_client_for_refuses_to_be_built_without_a_key() -> None:
    with pytest.raises(LlmError) as excinfo:
        llm.client_for(make_config(api_key=None))
    assert excinfo.value.code == "missing_api_key"


# ----------------------------------------------------------------- prompts


@pytest.mark.parametrize(
    ("version", "filename"),
    [
        ("segment/1", "segment.v1.ru.md"),
        ("extract/1", "extract.v1.ru.md"),
        ("recap/1", "recap.v1.ru.md"),
        ("glossary/1", "glossary.v1.ru.md"),
    ],
)
def test_prompt_files_exist_and_are_russian(version: str, filename: str) -> None:
    prompt = load_prompt(version)
    assert prompt.path.name == filename
    assert prompt.digest.startswith("sha256:")
    assert not prompt.text.startswith("<!--"), "the placeholder comment is stripped"
    cyrillic = sum(1 for ch in prompt.text if "а" <= ch.lower() <= "я")
    assert cyrillic > 200, "the table speaks Russian; so do the prompts"


@pytest.mark.parametrize("version", ["segment/1", "extract/1", "recap/1", "glossary/1"])
def test_every_declared_placeholder_is_renderable(version: str) -> None:
    prompt = load_prompt(version)
    values = dict.fromkeys(prompt.placeholders(), "VALUE")
    assert values, "each prompt injects at least the lexicon"
    rendered = prompt.render(**values)
    assert "{" + next(iter(values)) + "}" not in rendered


def test_prompt_render_leaves_json_braces_alone() -> None:
    prompt = load_prompt("extract/1")
    rendered = prompt.render(**dict.fromkeys(prompt.placeholders(), "X"))
    assert "world_impact" in rendered


def test_unknown_prompt_version_is_a_typed_failure() -> None:
    with pytest.raises(PromptMissing):
        prompt_filename("nonsense")
    with pytest.raises(PromptMissing):
        load_prompt("segment/99")


def test_lexicon_reference_lists_active_terms_only(tmp_path) -> None:
    from .conftest import LEXICON_YAML

    path = tmp_path / "_lexicon.yaml"
    path.write_text(LEXICON_YAML, encoding="utf-8")
    reference = llm.lexicon_reference(load_lexicon(path))
    assert "Вазгар" in reference
    assert "Освальд" not in reference, "inactive terms are not injected"


# ------------------------------------------------------------- json schema


def test_object_schema_is_shaped_for_strict_mode() -> None:
    schema = object_schema({"a": {"type": "string"}, "b": {"type": "number"}})
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"a", "b"}


def test_nullable_widens_a_leaf_type() -> None:
    assert nullable({"type": "string"})["type"] == ["string", "null"]


def test_schema_errors_reports_dotted_paths() -> None:
    problems = schema_errors(SIMPLE_SCHEMA, BAD)
    assert any("items[0].name" in problem for problem in problems)
    assert any("items[0].kind" in problem for problem in problems)
    assert schema_errors(SIMPLE_SCHEMA, GOOD) == []


def test_schema_errors_tolerates_extra_keys() -> None:
    payload = {"items": [{"name": "x", "kind": "a", "surprise": 1}], "extra": True}
    assert schema_errors(SIMPLE_SCHEMA, payload) == []


def test_schema_errors_reports_a_missing_required_key() -> None:
    assert any("$.items: missing" in problem for problem in schema_errors(SIMPLE_SCHEMA, {}))


# ------------------------------------------------------------ structured call


def test_structured_call_returns_data_and_usage() -> None:
    client = FakeClient(constant(GOOD))
    data, usage, attempts = structured_call(
        config=make_config(),
        prompt_version="segment/1",
        system_prompt="система",
        user_content="реплики",
        schema=SIMPLE_SCHEMA,
        client=client,
    )
    assert data == GOOD
    assert attempts == 1
    assert usage == Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, calls=1)
    request = client.calls[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["response_format"]["json_schema"]["schema"] == SIMPLE_SCHEMA


def test_structured_call_retries_once_with_the_error_appended() -> None:
    client = FakeClient(sequence(BAD, GOOD))
    data, usage, attempts = structured_call(
        config=make_config(),
        prompt_version="segment/1",
        system_prompt="система",
        user_content="реплики",
        schema=SIMPLE_SCHEMA,
        client=client,
    )
    assert data == GOOD
    assert attempts == 2
    assert usage.calls == 2, "both calls are paid for and reported"
    repair = client.calls[1]["messages"]
    assert repair[-2]["role"] == "assistant", "the bad answer stays in the conversation"
    assert "items[0].kind" in repair[-1]["content"], "the validation error is appended verbatim"


def test_structured_call_raises_after_the_retry_also_fails() -> None:
    client = FakeClient(constant(BAD))
    with pytest.raises(SchemaViolation) as excinfo:
        structured_call(
            config=make_config(),
            prompt_version="segment/1",
            system_prompt="система",
            user_content="реплики",
            schema=SIMPLE_SCHEMA,
            client=client,
        )
    assert len(client.calls) == 2
    assert excinfo.value.code == "schema_violation"
    assert excinfo.value.detail["problems"]


def test_non_json_output_is_treated_as_a_schema_violation() -> None:
    client = FakeClient(sequence("не json", json.dumps(GOOD)))
    data, _usage, attempts = structured_call(
        config=make_config(),
        prompt_version="segment/1",
        system_prompt="система",
        user_content="реплики",
        schema=SIMPLE_SCHEMA,
        client=client,
    )
    assert data == GOOD
    assert attempts == 2


def test_provider_failure_is_a_typed_error_not_a_schema_violation() -> None:
    client = FakeClient(constant(RuntimeError("connection reset")))
    with pytest.raises(LlmError) as excinfo:
        structured_call(
            config=make_config(),
            prompt_version="segment/1",
            system_prompt="система",
            user_content="реплики",
            schema=SIMPLE_SCHEMA,
            client=client,
        )
    assert excinfo.value.code == "openai_call_failed"
    assert len(client.calls) == 1, "a transport failure is map_reduce's to retry"


# --------------------------------------------------------------- map/reduce


def test_map_reduce_keeps_input_order_and_sums_usage() -> None:
    client = FakeClient(
        lambda kwargs, _i: {"items": [{"name": kwargs["messages"][1]["content"], "kind": "a"}]}
    )
    result = map_reduce(
        config=make_config(),
        prompt_version="extract/1",
        windows=["w0", "w1", "w2"],
        system_prompt="система",
        schema=SIMPLE_SCHEMA,
        render=lambda window: str(window),
        client=client,
    )
    assert [index for index, _ in result.succeeded()] == [0, 1, 2]
    assert [payload["items"][0]["name"] for _, payload in result.succeeded()] == ["w0", "w1", "w2"]
    assert result.usage.calls == 3
    assert result.usage.total_tokens == 45
    assert result.failed_windows() == []


def test_a_window_that_keeps_failing_is_reported_never_dropped() -> None:
    def handler(kwargs: dict, _index: int):
        if kwargs["messages"][1]["content"] == "w1":
            return RuntimeError("gateway timeout")
        return GOOD

    client = FakeClient(handler)
    result = map_reduce(
        config=make_config(),
        prompt_version="extract/1",
        windows=["w0", "w1", "w2"],
        system_prompt="система",
        schema=SIMPLE_SCHEMA,
        render=lambda window: str(window),
        client=client,
    )
    assert [index for index, _ in result.succeeded()] == [0, 2]
    failed = result.failed_windows()
    assert len(failed) == 1
    assert failed[0]["window"] == 1
    assert failed[0]["attempts"] == 3, "one call plus two retries"
    assert "gateway timeout" in failed[0]["error"]


def test_map_reduce_can_render_a_per_window_system_prompt() -> None:
    client = FakeClient(constant(GOOD))
    map_reduce(
        config=make_config(),
        prompt_version="extract/1",
        windows=[1, 2],
        system_prompt=lambda window: f"окно {window}",
        schema=SIMPLE_SCHEMA,
        render=lambda window: str(window),
        client=client,
    )
    assert client.system_prompts() == ["окно 1", "окно 2"]


def test_map_reduce_over_no_windows_costs_nothing() -> None:
    result = map_reduce(
        config=make_config(),
        prompt_version="extract/1",
        windows=[],
        system_prompt="система",
        schema=SIMPLE_SCHEMA,
        render=str,
        client=None,
    )
    assert result.windows == 0
    assert result.usage == Usage()


def test_usage_addition_is_total() -> None:
    total = Usage(1, 2, 3, 1) + Usage(10, 20, 30, 1)
    assert total.to_dict() == {
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "total_tokens": 33,
        "calls": 2,
    }
