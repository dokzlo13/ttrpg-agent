from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from image_gen.__main__ import main, make_request_config, parse_dotenv, plan_asset, slugify, write_asset_note


def test_parse_dotenv_basic() -> None:
    text = """
    # comment
    OPENAI_API_KEY=secret
    export TTRPG_IMAGE_MODEL='gpt-image-1'
    BAD LINE
    TTRPG_IMAGE_SIZE="1024x1024"
    """
    assert parse_dotenv(text) == {
        "OPENAI_API_KEY": "secret",
        "TTRPG_IMAGE_MODEL": "gpt-image-1",
        "TTRPG_IMAGE_SIZE": "1024x1024",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Old Wizard", "old-wizard"),
        ("  !!Ancient Elven Ruin??  ", "ancient-elven-ruin"),
        ("", "generated-image"),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_dry_run_json_creates_indexable_paths(tmp_path, monkeypatch) -> None:
    project = tmp_path
    (project / ".ttrpg" / "tools" / "image-gen").mkdir(parents=True)
    (project / "vault" / "notes" / "images").mkdir(parents=True)
    monkeypatch.setenv("TTRPG_ROOT", str(project))

    result = CliRunner().invoke(
        main,
        [
            "--subject",
            "Draw an ancient elven ruin at dawn, watercolor, no text, no watermark.",
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["image_path"].endswith(".png")
    assert payload["note_path"].endswith(".md")
    assert "/vault/notes/images/" in payload["image_path"]
    assert payload["request"]["provider"] == "openai"


def test_rejects_dest_outside_images(tmp_path, monkeypatch) -> None:
    project = tmp_path
    (project / ".ttrpg" / "tools" / "image-gen").mkdir(parents=True)
    monkeypatch.setenv("TTRPG_ROOT", str(project))

    result = CliRunner().invoke(
        main,
        ["--subject", "old wizard", "--dest", "vault/notes/other", "--dry-run"],
    )

    assert result.exit_code != 0
    assert "destination must be under vault/notes/images" in result.output


def test_requires_exactly_one_prompt_source(tmp_path, monkeypatch) -> None:
    project = tmp_path
    (project / ".ttrpg" / "tools" / "image-gen").mkdir(parents=True)
    monkeypatch.setenv("TTRPG_ROOT", str(project))

    result = CliRunner().invoke(main, ["--dry-run"])

    assert result.exit_code != 0
    assert "provide exactly one" in result.output


def test_asset_note_contains_frontmatter_prompt_and_json(tmp_path) -> None:
    config = make_request_config(
        prompt="Draw an original fantasy ruin, no text, no watermark.",
        title="Fantasy Ruin",
        slug="fantasy-ruin",
        model="gpt-image-1",
        size="1024x1024",
        quality="auto",
        output_format="png",
        dest="vault/notes/images",
    )
    planned = plan_asset(config, tmp_path)

    write_asset_note(
        planned=planned,
        response_metadata={"usage": {"input_tokens": 10}},
        prompt_source="test",
    )

    note = planned.note_path.read_text(encoding="utf-8")
    assert note.startswith("---\ntype: handout\nsource: agent")
    assert "asset_kind: image" in note
    assert "## Prompt" in note
    assert "Draw an original fantasy ruin" in note
    assert '"api_request"' in note
    assert '"output"' in note
    assert planned.image_path.name in note
