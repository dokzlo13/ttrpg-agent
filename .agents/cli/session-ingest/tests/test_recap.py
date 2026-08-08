"""``recap`` — extraction-only input, resolving links on every bullet, owner questions."""

from __future__ import annotations

from typing import Any

import craig_stt_dataset
import pytest

from session_ingest import adopt as adopt_mod
from session_ingest import recap as recap_mod
from session_ingest.errors import SessionIngestError
from session_ingest.recap import OWNER_SECTION, SECTIONS

from .conftest import SESSION_ID, Workspace
from .fakes import (
    FakeClient,
    adopt,
    constant,
    default_elements,
    evidence,
    make_config,
    turn_id_for,
    write_anchors,
    write_extraction,
)


def sections(**bullets: list[dict[str, Any]]) -> dict[str, Any]:
    """A recap answer: every heading present, most of them empty."""
    ru = {
        "scenes": "Сцены",
        "combat": "Бои",
        "social": "Социальные сцены",
        "loot": "Добыча",
        "quests": "Продвижение квестов",
        "faces": "Новые лица",
        "threads": "Открытые нити",
        "stop": "Точка остановки",
    }
    filled = {ru[key]: rows for key, rows in bullets.items()}
    return {
        "sections": [
            {"heading": heading, "bullets": filled.get(heading, [])} for heading in SECTIONS
        ]
    }


def _run(workspace: Workspace, client: FakeClient, monkeypatch, **kwargs):
    monkeypatch.setattr(recap_mod, "client_for", lambda _config: client)
    return recap_mod.run(
        roots=workspace.roots, config=make_config(), session_id=SESSION_ID, **kwargs
    )


def _prepared(workspace: Workspace) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    write_anchors(workspace)
    write_extraction(workspace)


def _draft(workspace: Workspace) -> str:
    return workspace.roots.session(SESSION_ID).recap_draft.read_text(encoding="utf-8")


DEFAULT_ANSWER = sections(
    scenes=[{"text": "Партия обыскала руины церкви", "evidence": [turn_id_for(0), turn_id_for(2)]}],
    stop=[{"text": "Остановились у алтаря", "evidence": [turn_id_for(4)]}],
)


# ------------------------------------------------------ the isolation rule


def test_recap_never_opens_the_dataset(workspace: Workspace, monkeypatch) -> None:
    """The recap distils the extraction. Reading verbatim speech here is how
    unevidenced claims creep in, so the dataset read path is booby-trapped."""
    _prepared(workspace)

    def explode(*_args, **_kwargs):
        raise AssertionError("recap must not open the dataset")

    monkeypatch.setattr(craig_stt_dataset, "open_dataset", explode)
    monkeypatch.setattr(adopt_mod, "open_dataset", explode)
    monkeypatch.setattr(adopt_mod, "resolve_active_dataset", explode)
    monkeypatch.setattr(adopt_mod, "open_verified", explode)

    payload = _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)
    assert payload["status"] == "ok"


def test_recap_without_a_key_skips_cleanly(workspace: Workspace) -> None:
    _prepared(workspace)
    payload = recap_mod.run(
        roots=workspace.roots, config=make_config(api_key=None), session_id=SESSION_ID
    )
    assert payload["status"] == "skipped"
    assert payload["code"] == "missing_api_key"
    assert not workspace.roots.session(SESSION_ID).recap_draft.exists()


def test_recap_without_an_extraction_names_extract(workspace: Workspace) -> None:
    adopt(workspace)
    write_anchors(workspace)
    with pytest.raises(SessionIngestError) as excinfo:
        recap_mod.run(roots=workspace.roots, config=make_config(), session_id=SESSION_ID)
    assert excinfo.value.code == "extraction_missing"
    assert excinfo.value.next_steps[0]["id"] == "extract"


# ------------------------------------------------------------------ the body


def test_every_bullet_ends_in_a_resolving_link(workspace: Workspace, monkeypatch) -> None:
    _prepared(workspace)
    payload = _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)

    assert payload["status"] == "ok"
    assert payload["bullets"] == 2
    assert payload["bullets_dropped"] == 0

    body = _draft(workspace)
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert bullets
    for line in bullets:
        assert line.rstrip().endswith("]]"), line
        assert f"[[transcripts/{SESSION_ID}/" in line


def test_all_headings_are_present_in_order(workspace: Workspace, monkeypatch) -> None:
    _prepared(workspace)
    _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)
    body = _draft(workspace)
    positions = [body.index(f"## {heading}") for heading in (*SECTIONS, OWNER_SECTION)]
    assert positions == sorted(positions)
    assert "_нет данных в выжимке_" in body, "an empty section says so rather than inventing"


def test_the_frontmatter_follows_the_vault_convention(workspace: Workspace, monkeypatch) -> None:
    _prepared(workspace)
    _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)
    body = _draft(workspace)
    head = body.split("---")[1]
    assert "type: session" in head
    assert "status: draft" in head
    assert "language: ru" in head
    assert "source: pipeline" in head
    assert f"transcript: {SESSION_ID}" in head
    assert "Вазгар" in head, "RU search terms come from the extraction's entities"


def test_a_bullet_citing_an_unknown_turn_is_dropped(workspace: Workspace, monkeypatch) -> None:
    _prepared(workspace)
    answer = sections(
        scenes=[
            {"text": "Подтверждено", "evidence": [turn_id_for(0)]},
            {"text": "Выдумано", "evidence": ["t-invented-9"]},
            {"text": "Совсем без ссылок", "evidence": []},
        ]
    )
    payload = _run(workspace, FakeClient(constant(answer)), monkeypatch)

    assert payload["bullets"] == 1
    assert payload["bullets_dropped"] == 2
    body = _draft(workspace)
    assert "Подтверждено" in body
    assert "Выдумано" not in body
    assert "Совсем без ссылок" not in body


def test_owner_questions_are_assembled_from_the_data_not_the_model(
    workspace: Workspace, monkeypatch
) -> None:
    _prepared(workspace)
    payload = _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)

    assert payload["owner_questions"] == 2
    body = _draft(workspace)
    owner_block = body.split(f"## {OWNER_SECTION}", 1)[1]
    assert "world_impact: local" in owner_block
    assert "требует решения" in owner_block
    for line in (line for line in owner_block.splitlines() if line.startswith("- ")):
        assert line.rstrip().endswith("]]")


def test_no_owner_questions_says_so(workspace: Workspace, monkeypatch) -> None:
    _prepared(workspace)
    elements = default_elements()
    elements["events"] = [
        {
            "id": "e1",
            "scene": None,
            "kind": "social",
            "summary": "Ничего важного",
            "outcome": None,
            "world_impact": "none",
            "needs_owner": False,
            "confidence": 0.5,
            "evidence": evidence(0),
        }
    ]
    write_extraction(workspace, elements=elements)
    payload = _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)
    assert payload["owner_questions"] == 0
    assert "_ничего не требует решения владельца_" in _draft(workspace)


def test_the_provenance_footer_records_what_produced_the_draft(
    workspace: Workspace, monkeypatch
) -> None:
    _prepared(workspace)
    _run(workspace, FakeClient(constant(DEFAULT_ANSWER)), monkeypatch)
    footer = _draft(workspace).split("> [!info] Провенанс", 1)[1]
    assert "sha256:" in footer
    assert "merge_gap_s: 1.5" in footer
    assert "extract/1 → recap/1" in footer


# ------------------------------------------------------------- skip-if-done


def test_recap_skips_on_the_second_run_and_audience_is_part_of_the_key(
    workspace: Workspace, monkeypatch
) -> None:
    _prepared(workspace)
    client = FakeClient(constant(DEFAULT_ANSWER))
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    assert _run(workspace, client, monkeypatch)["status"] == "skipped"
    assert len(client.calls) == 1
    assert _run(workspace, client, monkeypatch, audience="players")["status"] == "ok"
    assert len(client.calls) == 2


def test_only_the_extraction_reaches_the_model(workspace: Workspace, monkeypatch) -> None:
    _prepared(workspace)
    client = FakeClient(constant(DEFAULT_ANSWER))
    _run(workspace, client, monkeypatch)
    sent = client.user_contents()[0]
    assert "Старая часовня" in sent
    assert "Реплика 0" not in sent, "verbatim transcript text never reaches the recap prompt"
