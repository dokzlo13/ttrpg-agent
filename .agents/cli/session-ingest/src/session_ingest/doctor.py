"""``doctor`` — read-only facts about the environment this CLI is about to work in.

Nothing here writes, installs, repairs or decides. It answers the questions that
turn a confusing failure into an obvious one:

* Is the reader SDK older than the producer that wrote the datasets on disk?
  That is the one version skew that silently misreads a dataset, so it is the
  first thing reported.
* Do the three contract roots exist, and how much disk is each storage class
  holding? Disk is reported *by replaceability class* because that is the axis
  cleanup decisions are made on — irreplaceable source archives are counted
  apart from regenerable PCM.
* Is ``.agents/bin/craig-stt`` there, is an API key configured (presence only,
  never the value), is the ``transcripts`` qmd collection provisioned, and are
  the two hand-maintained vault files present?
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from craig_stt_dataset import DatasetError, known_datasets, open_dataset

from .config import SessionConfig
from .paths import Roots
from .vaultfiles import load_lexicon, load_speakers

SDK_PACKAGE = "craig-stt-dataset"

#: env keys doctor reports back; anything matching this is shown as presence only.
_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)

WORK_DIR_ENV = "CRAIG_STT_WORK_DIR"

#: Reported next to the resolved work dir, because the obvious way to redirect one
#: run does not work here and fails *silently*: ``.agents/env.sh`` exports
#: ``CRAIG_STT_WORK_DIR`` unconditionally, and ``.agents/bin/craig-stt`` sources it
#: after the caller's environment is already set, so an env prefix is overwritten
#: inside the launcher and the run lands in the corpus root anyway.
WORK_DIR_NOTE = (
    f"{WORK_DIR_ENV} is contract-owned: .agents/env.sh exports it unconditionally from "
    "TTRPG_SESSION_DATASETS_DIR, and .agents/bin/craig-stt sources that contract inside the "
    f"launcher — so a `{WORK_DIR_ENV}=… .agents/bin/craig-stt transcribe …` env prefix is "
    "clobbered and the run still writes here. Redirect a run with the GLOBAL `--work-dir "
    "<dir>` flag, which outranks the environment and must precede the subcommand."
)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", value):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break
    return tuple(parts)


def sdk_version() -> str | None:
    try:
        return package_version(SDK_PACKAGE)
    except PackageNotFoundError:
        return None


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


@dataclass(slots=True)
class DatasetProbe:
    """One dataset on disk, as doctor sees it without opening the segments."""

    path: str
    recording_id: str | None = None
    status: str | None = None
    schema_version: int | None = None
    produced_by: Mapping[str, str] = field(default_factory=dict)
    producer_sdk_version: str | None = None
    craig_stt_version: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "recording_id": self.recording_id,
            "status": self.status,
            "schema_version": self.schema_version,
            "produced_by": dict(self.produced_by),
            "producer_sdk_version": self.producer_sdk_version,
            "craig_stt_version": self.craig_stt_version,
            "error": self.error,
        }


def _producer_versions(produced_by: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Pull the SDK and CLI versions out of the manifest's ``produced_by`` map.

    Tolerant about the key spelling, since that is producer-side metadata this
    consumer must not depend on the exact shape of (CONTRACT rule 5).
    """
    sdk: str | None = None
    cli: str | None = None
    for raw_key, value in produced_by.items():
        key = str(raw_key).replace("_", "-").lower()
        if "dataset" in key:
            sdk = str(value)
        elif "craig-stt" in key or key == "craig":
            cli = str(value)
    return sdk, cli


def probe_datasets(datasets_root: Path, *, limit: int | None = None) -> list[DatasetProbe]:
    probes: list[DatasetProbe] = []
    for path in known_datasets(datasets_root):
        if limit is not None and len(probes) >= limit:
            break
        try:
            dataset = open_dataset(path)
        except DatasetError as exc:
            probes.append(DatasetProbe(path=str(path), error=str(exc)))
            continue
        sdk, cli = _producer_versions(dataset.manifest.produced_by)
        probe = DatasetProbe(
            path=str(path),
            recording_id=dataset.recording_id,
            status=dataset.status,
            schema_version=dataset.schema_version,
            produced_by=dict(dataset.manifest.produced_by),
            producer_sdk_version=sdk,
            craig_stt_version=cli,
        )
        if probe.craig_stt_version is None:
            # Fall back to the provenance block, which always carries it.
            try:
                probe.craig_stt_version = dataset.meta.provenance.craig_stt_version
            except DatasetError as exc:
                probe.error = str(exc)
        probes.append(probe)
    return probes


def qmd_collections(project_root: Path) -> dict[str, Any]:
    """Read-only report of the qmd index config. Never repaired from here."""
    index_yml = project_root / ".cache" / "index" / "index.yml"
    result: dict[str, Any] = {"path": str(index_yml), "readable": False, "transcripts": None}
    if not index_yml.is_file():
        return result
    try:
        import yaml

        payload = yaml.safe_load(index_yml.read_text(encoding="utf-8"))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["readable"] = True
    collections = payload.get("collections") if isinstance(payload, dict) else None
    if not isinstance(collections, dict):
        return result
    result["collections"] = sorted(collections)
    entry = collections.get("transcripts")
    if isinstance(entry, dict):
        result["transcripts"] = {
            "path": entry.get("path"),
            "pattern": entry.get("pattern"),
            # Absent means the qmd default (True); the contract wants it excluded.
            "include_by_default": entry.get("includeByDefault", True),
        }
    return result


def craig_env(env: Mapping[str, str]) -> dict[str, Any]:
    """The resolved ``CRAIG_STT_*`` surface, with anything secret-shaped redacted."""
    resolved: dict[str, Any] = {}
    for key in sorted(env):
        if not key.startswith("CRAIG_STT_"):
            continue
        resolved[key] = "<present>" if _SECRET_RE.search(key) else env[key]
    return resolved


@dataclass(slots=True)
class DoctorReport:
    status: str
    sdk: dict[str, Any]
    roots: dict[str, Any]
    disk: dict[str, Any]
    datasets: list[dict[str, Any]]
    sessions: dict[str, Any]
    tools: dict[str, Any]
    llm: dict[str, Any]
    qmd: dict[str, Any]
    vault_files: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)
    next_steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sdk": self.sdk,
            "roots": self.roots,
            "disk": self.disk,
            "datasets": self.datasets,
            "sessions": self.sessions,
            "tools": self.tools,
            "llm": self.llm,
            "qmd": self.qmd,
            "vault_files": self.vault_files,
            "findings": self.findings,
            "next_steps": self.next_steps,
        }


def _finding(code: str, severity: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "detail": detail}


def run_doctor(
    *,
    roots: Roots,
    project_root: Path,
    config: SessionConfig,
    env: Mapping[str, str] | None = None,
) -> DoctorReport:
    resolved_env = dict(os.environ) if env is None else dict(env)
    findings: list[dict[str, Any]] = []

    reader = sdk_version()
    probes = probe_datasets(roots.datasets)
    producer_versions = sorted(
        {p.producer_sdk_version for p in probes if p.producer_sdk_version is not None}
    )
    newest_producer = max(producer_versions, key=_version_tuple, default=None)
    if (
        reader is not None
        and newest_producer is not None
        and _version_tuple(reader) < _version_tuple(newest_producer)
    ):
        findings.append(
            _finding(
                "sdk_older_than_producer",
                "warning",
                f"craig-stt-dataset {reader} is older than the {newest_producer} that wrote "
                f"a dataset on disk; re-pin this project to at least that revision.",
                reader=reader,
                producer=newest_producer,
            )
        )
    if reader is None:
        findings.append(
            _finding("sdk_missing", "error", "craig-stt-dataset is not installed in this venv")
        )

    root_state = {
        name: {"path": path, "exists": Path(path).is_dir()}
        for name, path in roots.to_dict().items()
    }
    for name, state in root_state.items():
        if not state["exists"] and name != "scratch":
            findings.append(
                _finding(
                    "root_missing",
                    "warning",
                    f"{name} root does not exist yet: {state['path']}",
                    root=name,
                )
            )

    session_ids = roots.known_session_ids()
    derived_bytes = {
        session_id: _dir_bytes(roots.sessions / session_id) for session_id in session_ids
    }
    datasets_bytes = _dir_bytes(roots.datasets)
    scratch_bytes = _dir_bytes(roots.scratch)
    try:
        usage = shutil.disk_usage(roots.sessions if roots.sessions.exists() else project_root)
        free_bytes: int | None = usage.free
    except OSError:
        free_bytes = None

    disk = {
        "datasets_bytes": datasets_bytes,
        "session_derived_bytes": sum(derived_bytes.values()),
        "session_derived_by_session": derived_bytes,
        "scratch_bytes": scratch_bytes,
        "transcripts_bytes": _dir_bytes(roots.transcripts),
        "free_bytes": free_bytes,
        "note": (
            "WSL VHDX does not shrink when files are deleted; reclaiming disk inside WSL "
            "does not return it to Windows without a manual compaction."
        ),
    }

    craig_launcher = project_root / ".agents" / "bin" / "craig-stt"
    work_dir_in_env = (resolved_env.get(WORK_DIR_ENV) or "").strip() or None
    tools = {
        "craig_stt_work_dir": str(roots.datasets),
        "craig_stt_work_dir_source": "TTRPG_SESSION_DATASETS_DIR",
        "craig_stt_work_dir_in_env": work_dir_in_env,
        "craig_stt_work_dir_note": WORK_DIR_NOTE,
        "craig_stt_launcher": str(craig_launcher),
        "craig_stt_launcher_present": craig_launcher.is_file(),
        "craig_stt_launcher_executable": os.access(craig_launcher, os.X_OK)
        if craig_launcher.exists()
        else False,
        "craig_stt_state_config": str(project_root / ".agents" / "state" / "craig-stt.toml"),
        "craig_stt_state_config_present": (
            project_root / ".agents" / "state" / "craig-stt.toml"
        ).is_file(),
        "craig_stt_env": craig_env(resolved_env),
    }
    if not tools["craig_stt_launcher_present"]:
        findings.append(
            _finding(
                "craig_stt_launcher_missing",
                "warning",
                "`.agents/bin/craig-stt` is absent; transcription cannot be launched through "
                "the environment contract.",
            )
        )
    if work_dir_in_env is not None and Path(work_dir_in_env).expanduser() != roots.datasets:
        findings.append(
            _finding(
                "craig_stt_work_dir_overridden",
                "warning",
                f"{WORK_DIR_ENV}={work_dir_in_env} differs from TTRPG_SESSION_DATASETS_DIR "
                f"({roots.datasets}); the launcher re-exports the contract value, so this "
                f"override does not survive. Use the global `--work-dir` flag instead.",
                env_value=work_dir_in_env,
                datasets_root=str(roots.datasets),
            )
        )

    lexicon = load_lexicon(roots.lexicon_file)
    speakers = load_speakers(roots.speakers_file)
    if not speakers.present:
        findings.append(
            _finding(
                "speakers_missing",
                "warning",
                f"{roots.speakers_file} is absent; every discord id will be reported unmapped. "
                f"`qa` lists the ids to seed it from.",
            )
        )
    if not lexicon.present:
        findings.append(
            _finding(
                "lexicon_missing",
                "info",
                f"{roots.lexicon_file} is absent; lexicon_miss_rate cannot be measured and "
                f"biasing files cannot be planned.",
            )
        )

    qmd = qmd_collections(project_root)
    transcripts_entry = qmd.get("transcripts")
    if qmd.get("readable") and transcripts_entry is None:
        findings.append(
            _finding(
                "qmd_transcripts_collection_missing",
                "warning",
                "the `transcripts` qmd collection is not provisioned; open a fresh shell so "
                "`.agents/env.sh` can create it.",
            )
        )
    elif isinstance(transcripts_entry, dict) and transcripts_entry.get("include_by_default"):
        findings.append(
            _finding(
                "qmd_transcripts_included_by_default",
                "warning",
                "the `transcripts` collection is searched by default; prep retrieval would see "
                "verbatim table speech.",
            )
        )

    severities = {f["severity"] for f in findings}
    status = "failed" if "error" in severities else ("review" if "warning" in severities else "ok")

    return DoctorReport(
        status=status,
        sdk={
            "package": SDK_PACKAGE,
            "reader_version": reader,
            "producer_versions_on_disk": producer_versions,
            "newest_producer_version": newest_producer,
        },
        roots=root_state,
        disk=disk,
        datasets=[p.to_dict() for p in probes],
        sessions={"count": len(session_ids), "ids": session_ids},
        tools=tools,
        llm={
            "openai_api_key_present": config.api_key_present,
            "model": config.openai_model,
            "model_source": config.source("openai_model"),
            "max_concurrency": config.openai_max_concurrency,
        },
        qmd=qmd,
        vault_files={"lexicon": lexicon.to_dict(), "speakers": speakers.to_dict()},
        findings=findings,
        next_steps=[],
    )
