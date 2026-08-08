"""``plan`` — one ranked list, emitted twice with opposite truncation."""

from __future__ import annotations

from pathlib import Path

import pytest

from session_ingest import llm, plan
from session_ingest.config import resolve_config
from session_ingest.errors import SessionIngestError

from .conftest import MORPH_LEXICON_YAML, SESSION_ID, Workspace

#: `kilverin` is priority 5 and `vagzar` priority 10, so 1-is-highest puts
#: Кильверин ahead of Вазгар. `oswald` is inactive and never competes.
FIXTURE_ORDER = ["Кильверин", "Вазгар"]

#: Both terms share a priority, so only the observed count can separate them —
#: and alphabetically Абак would win, so a count-aware ranking is visible.
COUNTED_LEXICON = """\
terms:
  - id: late
    canonical: Ярл
    display_ru: Ярл
    variants: [Вагзар, Вазагар]
    active: true
    priority: 5
  - id: early
    canonical: Абак
    active: true
    priority: 5
"""


def _plan(workspace: Workspace, **kwargs):
    return plan.run(
        roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID, **kwargs
    )


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


# ------------------------------------------------------------------ ordering


def test_emits_both_files_with_opposite_order(workspace: Workspace) -> None:
    workspace.write_lexicon()
    result = _plan(workspace)

    assert result["status"] == "ok"
    assert result["prompt_terms"] == FIXTURE_ORDER
    assert result["hotwords_terms"] == list(reversed(FIXTURE_ORDER))

    hotwords = _read(result["hotwords_file"])
    prompt = _read(result["initial_prompt_file"])
    assert hotwords == "Вазгар Кильверин"
    assert hotwords.split()[-1] == FIXTURE_ORDER[0], "hotwords truncate from the front: best LAST"
    assert prompt == "В записи упоминаются: Кильверин, Вазгар."
    assert prompt.index("Кильверин") < prompt.index("Вазгар"), (
        "the initial prompt truncates from the back: best FIRST"
    )


def test_display_ru_is_what_reaches_the_files(workspace: Workspace) -> None:
    workspace.write_lexicon(
        "terms:\n  - id: vagzar\n    canonical: Vagzar\n    display_ru: Вазгар\n    priority: 1\n"
    )
    result = _plan(workspace)
    assert result["hotwords_terms"] == ["Вазгар"]
    assert "Vagzar" not in _read(result["hotwords_file"])


def test_inactive_terms_never_reach_the_biasing_files(workspace: Workspace) -> None:
    workspace.write_lexicon()
    result = _plan(workspace)
    assert "Освальд" not in _read(result["initial_prompt_file"])


def test_unset_priority_sorts_below_every_explicit_one(workspace: Workspace) -> None:
    workspace.write_lexicon(
        "terms:\n"
        "  - id: unset\n    canonical: Ааа\n"
        "  - id: explicit\n    canonical: Яяя\n    priority: 9\n"
    )
    result = _plan(workspace)
    assert result["prompt_terms"] == ["Яяя", "Ааа"]


# --------------------------------------------------------------- the budget


def test_budget_is_respected_by_both_files(workspace: Workspace) -> None:
    workspace.write_lexicon()
    for budget in (25, 60, 450):
        result = _plan(workspace, budget_chars=budget, force=True)
        assert len(_read(result["hotwords_file"])) <= budget, budget
        assert len(_read(result["initial_prompt_file"])) <= budget, budget


def test_a_tight_budget_keeps_the_best_terms(workspace: Workspace) -> None:
    workspace.write_lexicon()
    result = _plan(workspace, budget_chars=15)
    # Кильверин outranks Вазгар, so it is the one that survives the squeeze.
    assert result["hotwords_terms"] == ["Кильверин"]
    assert any("did not fit" in warning for warning in result["warnings"])


def test_fit_helpers_truncate_in_opposite_directions() -> None:
    terms = ["aaaa", "bbbb", "cccc"]
    assert plan.fit_hotwords(terms, 9) == ["aaaa", "bbbb"]
    assert plan.hotwords_text(plan.fit_hotwords(terms, 9)) == "bbbb aaaa"
    assert plan.fit_prompt(terms, len(plan.PROMPT_PREFIX) + 5) == ["aaaa"]
    assert plan.prompt_text(["aaaa"]).startswith(plan.PROMPT_PREFIX)


def test_a_zero_budget_is_refused(workspace: Workspace) -> None:
    workspace.write_lexicon()
    with pytest.raises(SessionIngestError) as excinfo:
        _plan(workspace, budget_chars=0)
    assert excinfo.value.code == "invalid_budget"


# --------------------------------------------------------- the entities file


def test_entities_file_terms_are_merged_at_the_documented_priority(
    workspace: Workspace, tmp_path: Path
) -> None:
    workspace.write_lexicon()
    entities = tmp_path / "arc.txt"
    entities.write_text("# active arc\nКроненфельд\n\nКроненфельд\n", encoding="utf-8")

    result = _plan(workspace, entities_file=entities)
    rows = {row["term"]: row for row in result["candidates"]}
    assert rows["Кроненфельд"]["priority"] == plan.ENTITIES_PRIORITY
    assert rows["Кроненфельд"]["source"] == "entities-file"
    # priority 5 ties with Кильверин, broken alphabetically; Вазгар (10) stays last
    assert result["prompt_terms"] == ["Кильверин", "Кроненфельд", "Вазгар"]


def test_an_entities_line_that_names_a_known_term_is_not_duplicated(
    workspace: Workspace, tmp_path: Path
) -> None:
    workspace.write_lexicon()
    entities = tmp_path / "arc.txt"
    entities.write_text("Вагзар\nкильверин\n", encoding="utf-8")

    result = _plan(workspace, entities_file=entities)
    assert [row["term"] for row in result["candidates"]] == FIXTURE_ORDER
    assert all(row["source"] == "lexicon" for row in result["candidates"])


def test_a_missing_entities_file_is_a_clean_failure(workspace: Workspace) -> None:
    workspace.write_lexicon()
    with pytest.raises(SessionIngestError) as excinfo:
        _plan(workspace, entities_file=Path("/nonexistent/arc.txt"))
    assert excinfo.value.code == "entities_file_missing"


# ------------------------------------------------------------ observed counts


def test_a_rerun_ranks_by_this_sessions_observed_counts(workspace: Workspace) -> None:
    workspace.write_lexicon(COUNTED_LEXICON)
    workspace.adopt()

    first = _plan(workspace, run=1)
    assert first["prompt_terms"] == ["Абак", "Ярл"], "no dataset yet: priority then alphabetical"
    assert first["ranking"] == "priority+name"

    second = _plan(workspace, run=2)
    assert second["ranking"] == "priority+count"
    assert second["counts_from"] == workspace.recording_id
    counts = {row["term"]: row["count"] for row in second["candidates"]}
    assert counts == {"Ярл": 3, "Абак": 0}  # Вагзар ×2 + Вазагар ×1, exact strings only
    assert second["prompt_terms"] == ["Ярл", "Абак"]


def test_a_rerun_without_an_adopted_dataset_falls_back_and_says_so(
    workspace: Workspace,
) -> None:
    workspace.write_lexicon()
    result = _plan(workspace, run=2)
    assert result["ranking"] == "priority+name"
    assert any("without observed counts" in warning for warning in result["warnings"])


# ------------------------------------------------------------------ determinism


def test_two_runs_produce_byte_identical_files(workspace: Workspace) -> None:
    workspace.write_lexicon()
    first = _plan(workspace)
    before = (
        Path(first["hotwords_file"]).read_bytes(),
        Path(first["initial_prompt_file"]).read_bytes(),
    )
    second = _plan(workspace, force=True)
    after = (
        Path(second["hotwords_file"]).read_bytes(),
        Path(second["initial_prompt_file"]).read_bytes(),
    )
    assert before == after


def test_the_same_lexicon_key_skips_and_force_rewrites(workspace: Workspace) -> None:
    workspace.write_lexicon()
    assert _plan(workspace)["status"] == "ok"
    assert _plan(workspace)["status"] == "skipped"
    assert _plan(workspace, force=True)["status"] == "ok"
    # a grown lexicon moves the key
    workspace.write_lexicon(
        workspace.roots.lexicon_file.read_text(encoding="utf-8")
        + "  - id: extra\n    canonical: Кроненфельд\n"
    )
    assert _plan(workspace)["status"] == "ok"


# ---------------------------------------------------------------- next steps


def test_next_steps_carry_the_exact_transcribe_command(workspace: Workspace) -> None:
    workspace.write_lexicon()
    result = _plan(workspace)
    steps = {entry["id"]: entry for entry in result["next_steps"]}
    assert list(steps) == ["craig_transcribe", "adopt"]
    command = steps["craig_transcribe"]["command"]
    assert command == ".agents/bin/craig-stt transcribe '<share-url>' --json"
    # craig-stt answers with one JSON object too; an agent walking the chain reads
    # the recording id and digest from it instead of scraping the human table.
    assert "JSON object on stdout" in steps["craig_transcribe"]["summary"]
    assert f"--session {SESSION_ID}" in steps["adopt"]["command"]


# ------------------------------------------------------- the biasing posture


def test_the_emitted_command_omits_the_biasing_files_by_default(workspace: Workspace) -> None:
    """Measured on a real session: biasing produced prompt echo, not corrections.

    Whole turns came back as the term list, compression outliers went 6 → 85, and
    lexicon hits grew by 412 on 130 extra words — a `lexicon_miss_rate` that fell
    for the wrong reason. So the flags are opt-in, while the files stay written:
    they cost nothing and are useful to read.
    """
    workspace.write_lexicon()
    result = _plan(workspace)
    command = next(
        entry["command"] for entry in result["next_steps"] if entry["id"] == "craig_transcribe"
    )
    assert "--hotwords-file" not in command
    assert "--initial-prompt-file" not in command
    assert result["with_biasing"] is False
    assert Path(result["hotwords_file"]).is_file()
    assert Path(result["initial_prompt_file"]).is_file()
    summary = next(
        entry["summary"] for entry in result["next_steps"] if entry["id"] == "craig_transcribe"
    )
    assert "EXPERIMENTAL" in summary
    assert "--with-biasing" in summary
    assert any("--with-biasing" in line for line in result["lines"])


def test_with_biasing_puts_both_files_back_into_the_command(workspace: Workspace) -> None:
    workspace.write_lexicon()
    result = _plan(workspace, with_biasing=True)
    command = next(
        entry["command"] for entry in result["next_steps"] if entry["id"] == "craig_transcribe"
    )
    assert f"--hotwords-file {result['hotwords_file']}" in command
    assert f"--initial-prompt-file {result['initial_prompt_file']}" in command
    assert command.endswith(" --json")
    assert result["with_biasing"] is True


def test_asking_for_biasing_after_the_fact_does_not_force_a_rewrite(
    workspace: Workspace,
) -> None:
    """The flag changes the command, not the files, so it must not move the key."""
    workspace.write_lexicon()
    assert _plan(workspace)["status"] == "ok"
    second = _plan(workspace, with_biasing=True)
    assert second["status"] == "skipped"
    command = next(
        entry["command"] for entry in second["next_steps"] if entry["id"] == "craig_transcribe"
    )
    assert "--hotwords-file" in command


def test_a_rerun_points_at_a_scratch_work_dir_and_the_local_archive(
    workspace: Workspace,
) -> None:
    workspace.write_lexicon()
    workspace.adopt()
    result = _plan(workspace, run=3)
    steps = {entry["id"]: entry for entry in result["next_steps"]}
    assert list(steps) == ["craig_transcribe_rerun", "adopt"]
    command = steps["craig_transcribe_rerun"]["command"]
    scratch = f"{workspace.roots.scratch}/{SESSION_ID}-r3"
    # The work dir is a GLOBAL craig-stt flag and must precede the subcommand; an
    # env prefix is re-exported away by .agents/env.sh inside the launcher, which
    # would drop the re-run on top of the dataset it is being compared against.
    assert command.startswith(f".agents/bin/craig-stt --work-dir {scratch} transcribe ")
    assert "CRAIG_STT_WORK_DIR=" not in command
    assert command.index("--work-dir") < command.index("transcribe")
    assert "--hotwords-file" not in command
    assert command.endswith(" --json")
    assert "--run 3" in steps["adopt"]["command"]


def test_run_zero_is_refused(workspace: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        _plan(workspace, run=0)
    assert excinfo.value.code == "invalid_run"


# ---------------------------------------------------------------------- rank


def test_rank_without_a_key_skips_cleanly(workspace: Workspace) -> None:
    workspace.write_lexicon()
    result = _plan(workspace, rank=True)
    assert result["status"] == "skipped"
    assert result["metered"] is True
    assert [entry["id"] for entry in result["next_steps"]] == ["plan_deterministic"]
    assert not workspace.roots.session(SESSION_ID).hotwords_file.exists()


def _rank_result(terms: list[str], **usage: int) -> llm.StructuredResult:
    """What ``llm.structured_call`` really returns — a NamedTuple, not a Mapping.

    The fake has to be shaped exactly like the real return value: a plain
    ``{"terms": [...]}`` dict made ``_rank_with_llm``'s Mapping check pass in the
    fake and fail in production, which is how a paid-for ranking silently
    degraded to the deterministic one for a whole wave.
    """
    return llm.StructuredResult(
        data={"terms": terms},
        usage=llm.Usage(**usage) if usage else llm.Usage(total_tokens=42, calls=1),
        attempts=1,
    )


def test_rank_uses_the_llm_order_but_never_its_inventions(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.write_lexicon()
    config = resolve_config(env=workspace.plain_env(OPENAI_API_KEY="sk-test"))
    calls: list[dict] = []

    def fake_structured_call(**kwargs):
        calls.append(kwargs)
        return _rank_result(["Вазгар", "Совершенно выдуманный термин"])

    monkeypatch.setattr(llm, "structured_call", fake_structured_call)
    result = plan.run(roots=workspace.roots, config=config, session_id=SESSION_ID, rank=True)

    assert result["ranking"] == "llm"
    assert calls[0]["prompt_version"] == plan.PROMPT_VERSION
    # the LLM's order is honoured, its invention is dropped, and nothing it forgot is lost
    assert result["prompt_terms"] == ["Вазгар", "Кильверин"]
    # ... and the order it asked for is genuinely *different* from the free one,
    # so a silent fallback to the deterministic ranking cannot pass this test.
    assert result["prompt_terms"] != FIXTURE_ORDER
    assert result["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 42,
        "calls": 1,
    }


def test_a_useless_rank_response_falls_back_to_the_deterministic_order(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.write_lexicon()
    config = resolve_config(env=workspace.plain_env(OPENAI_API_KEY="sk-test"))
    monkeypatch.setattr(llm, "structured_call", lambda **_: _rank_result([]))
    result = plan.run(roots=workspace.roots, config=config, session_id=SESSION_ID, rank=True)
    assert result["prompt_terms"] == FIXTURE_ORDER


# -------------------------------------------------------- morph containment


def test_morph_terms_bias_with_the_lemma_only(workspace: Workspace) -> None:
    """Generated declensions must never reach hotwords or the initial prompt.

    The biasing budget is ~450 characters of acoustic hints; spending it on six
    endings of one name would evict five other names for nothing. `plan` is the
    one stage that deliberately does not consult the expansion table, and this
    is the test that keeps it that way.
    """
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    result = _plan(workspace)

    assert {candidate["term"] for candidate in result["candidates"]} == {"Морвика", "Морвики"}
    everything = result["hotwords"] + "\n" + result["initial_prompt"]
    for inflected in ("Морвике", "Морвику", "Морвикой", "Марвике", "Марвикой"):
        assert inflected not in everything, inflected
    assert "Морвика" in everything
