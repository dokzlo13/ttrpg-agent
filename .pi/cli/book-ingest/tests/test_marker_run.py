import json
from pathlib import Path

import pytest

from book_ingest.config import LLMConfig
from book_ingest.marker_run import MarkerInvocation, _build_command, _write_llm_config


def _inv(*, llm_enabled: bool, describe_images: bool, mode: str | None = None) -> MarkerInvocation:
    llm = LLMConfig(
        enabled=llm_enabled,
        enabled_source="cli",
        api_key="k" if llm_enabled else None,
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        mode=mode or (("all" if describe_images else "text-only") if llm_enabled else "no"),
    )
    return MarkerInvocation(
        workers=1,
        device="auto",
        layout_batch_size=None,
        detection_batch_size=None,
        recognition_batch_size=None,
        table_rec_batch_size=None,
        page_range=None,
        force_ocr=False,
        llm=llm,
        describe_images=describe_images,
    )


def _llm(mode: str = "all") -> LLMConfig:
    return LLMConfig(
        enabled=True,
        enabled_source="cli",
        api_key="sekrit",
        model="m",
        base_url="https://x/v1",
        mode=mode,
    )


def test_paginate_only_for_markdown():
    inv = _inv(llm_enabled=False, describe_images=False)
    md_cmd = _build_command(Path("x.pdf"), Path("/tmp/out"), "markdown", None, inv)
    json_cmd = _build_command(Path("x.pdf"), Path("/tmp/out"), "json", None, inv)
    assert "--paginate_output" in md_cmd
    assert "--paginate_output" not in json_cmd


@pytest.mark.parametrize("output_format", ["markdown", "json"])
def test_use_llm_emits_config_json_arg(output_format: str):
    inv = _inv(llm_enabled=True, describe_images=False)
    cfg = Path("/tmp/cfg.json")
    cmd = _build_command(Path("x.pdf"), Path("/tmp/out"), output_format, cfg, inv)
    assert "--use_llm" in cmd
    assert "--llm_service" in cmd
    assert "--config_json" in cmd
    idx = cmd.index("--config_json")
    assert cmd[idx + 1] == str(cfg)


def test_no_use_llm_omits_llm_args():
    inv = _inv(llm_enabled=False, describe_images=False)
    cmd = _build_command(Path("x.pdf"), Path("/tmp/out"), "markdown", None, inv)
    assert "--use_llm" not in cmd
    assert "--llm_service" not in cmd


def test_images_only_llm_only_applies_to_markdown_and_overrides_processors():
    inv = _inv(llm_enabled=True, describe_images=True, mode="images-only")
    cfg = Path("/tmp/cfg.json")
    md_cmd = _build_command(Path("x.pdf"), Path("/tmp/out"), "markdown", cfg, inv)
    json_cmd = _build_command(Path("x.pdf"), Path("/tmp/out"), "json", cfg, inv)
    assert "--use_llm" in md_cmd
    assert "--processors" in md_cmd
    processors = md_cmd[md_cmd.index("--processors") + 1]
    assert "LLMImageDescriptionProcessor" in processors
    assert "LLMTableProcessor" not in processors
    assert "--use_llm" not in json_cmd
    assert "--processors" not in json_cmd


def test_write_llm_config_describes_only_in_markdown(tmp_path):
    md_path = _write_llm_config(tmp_path, _llm(), describe_images=True, output_format="markdown")
    json_path = _write_llm_config(tmp_path, _llm(), describe_images=True, output_format="json")
    assert md_path != json_path
    md = json.loads(md_path.read_text())
    js = json.loads(json_path.read_text())
    assert md.get("LLMImageDescriptionProcessor_extract_images") is False
    assert "LLMImageDescriptionProcessor_extract_images" not in js


def test_write_llm_config_omits_override_when_describe_off(tmp_path):
    p = _write_llm_config(tmp_path, _llm(), describe_images=False, output_format="markdown")
    payload = json.loads(p.read_text())
    assert "LLMImageDescriptionProcessor_extract_images" not in payload
    assert payload["openai_api_key"] == "sekrit"


def test_write_llm_config_images_only_disables_line_merge_llm_toggle(tmp_path):
    p = _write_llm_config(
        tmp_path,
        _llm("images-only"),
        describe_images=True,
        output_format="markdown",
        llm_mode="images-only",
    )
    payload = json.loads(p.read_text())
    assert payload["LLMImageDescriptionProcessor_extract_images"] is False
    assert payload["LineMergeProcessor_use_llm"] is False


def test_write_llm_config_writes_secret_to_owner_only_file(tmp_path):
    p = _write_llm_config(tmp_path, _llm(), describe_images=False, output_format="markdown")
    mode = p.stat().st_mode & 0o777
    assert mode == 0o600
