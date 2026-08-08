"""``prune`` — reclaim the regenerable gigabytes. Deterministic.

Contract (DESIGN §1, §4; CONTRACT rule 2):

* Delete **only** through ``craig_stt_dataset.prune()``. An ``rm`` would leave
  the manifest declaring files and digests that are gone, which the next
  ``verify=True`` would report as corruption rather than the intended state it is.
* Prunable roles are ``pcm``, ``stt_cache`` and ``audio_extracted``
  (``source/extracted``). The source **archive** is never prunable: Craig expires
  recordings, so it is the one irreplaceable artifact in the tree.
* Refuse **deletion** when the session chronicle has not been written — pruning
  before the record is approved throws away the ability to re-transcribe.
* ``--dry-run`` reports the inventory and the bytes that would be freed, and is
  allowed without a chronicle: it deletes nothing, and "how much would this
  reclaim?" is exactly the question asked before the chronicle exists. The
  answer carries a warning saying the real prune is still refused.
* Say the WSL thing in the output: the VHDX does not shrink on delete, so freed
  space does not return to Windows without a manual compaction.

Two boundaries this verb draws that the SDK does not.

**``stt_cache`` is not pruned by default.** The per-track STT cache is what makes
a re-render or a merge-gap experiment free; ``pcm`` and ``audio_extracted``
regenerate from the archive in seconds of ffmpeg, the STT cache costs another GPU
pass. It is reported in the inventory as available and left alone, because this
CLI has no flag to ask for it and inventing one silently is worse than saying so.

**Scratch datasets are refused outright.** An A/B work dir under
``.cache/sessions/scratch/`` is plain-deletable — ``rm -rf`` on the directory is
the correct and complete answer there, and running a manifest-rewriting prune
against a disposable tree only makes it look precious.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from craig_stt_dataset import DatasetError, prunable_roles
from craig_stt_dataset import prune as sdk_prune

from .adopt import resolve_active_dataset
from .config import SessionConfig
from .errors import SessionIngestError
from .nextsteps import CLI, next_steps_for, step
from .paths import Roots

#: Roles this verb prunes unless told otherwise. The SDK's manifest is the authority
#: on what is *allowed*; this is the subset that is cheap to regenerate.
DEFAULT_ROLES = ("pcm", "audio_extracted")
#: Prunable, but a re-transcription's worth of GPU to rebuild — never automatic.
DEFERRED_ROLES = ("stt_cache",)
#: Kept here as documentation; the SDK's ``prunable`` flag is what actually decides.
PRUNABLE_ROLES = ("pcm", "stt_cache", "audio_extracted")

WSL_NOTE = (
    "WSL VHDX does not shrink when files are deleted: the space is free inside WSL but "
    "does not return to Windows without a manual compaction."
)


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def measure(target: Path) -> int:
    """Bytes on disk for one role's path, so the report can break the total down."""
    if not target.exists():
        return 0
    if target.is_file():
        return target.stat().st_size
    total = 0
    for child in target.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def is_under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:  # pragma: no cover - resolve on a vanished path
        return False


@dataclass(frozen=True, slots=True)
class RoleInventory:
    role: str
    path: str
    bytes: int
    pruned: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "bytes": self.bytes,
            "pruned": self.pruned,
            "reason": self.reason,
        }


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    project_root: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Returns the ``--json`` payload: roles pruned, paths, bytes freed, dry_run flag."""
    tree = roots.session(session_id)
    recording, dataset = resolve_active_dataset(tree, None)
    dataset_dir = Path(recording.dataset_path)

    if is_under(dataset_dir, roots.scratch):
        raise SessionIngestError(
            f"{dataset_dir} is a scratch A/B work dir, not the session's dataset. Scratch dirs "
            f"are disposable in full — delete the directory instead of pruning roles out of it, "
            f"and adopt --promote the run you want to keep first.",
            code="scratch_dataset",
            detail={"dataset_path": str(dataset_dir), "scratch_root": str(roots.scratch)},
        )

    chronicles = tree.chronicle_candidates(project_root) if project_root is not None else []
    warnings: list[str] = []
    if not chronicles:
        # The gate stops *deletion*, not inspection: a dry run removes nothing, and
        # "how much would this reclaim?" is precisely the question asked before the
        # chronicle is written. Both escapes still say so in the payload.
        if not force and not dry_run:
            raise SessionIngestError(
                f"no session chronicle mentions {session_id} under vault/notes/sessions/. "
                f"Pruning throws away the decoded audio a re-transcription would need, so it is "
                f"refused until the record is written and approved. Pass --force to override.",
                code="chronicle_missing",
                detail={"session": session_id},
                next_steps=[
                    step(
                        "record",
                        "Assemble record.json, then let the agent draft the chronicle from it.",
                        command=f"{CLI} record --session {session_id}",
                    ),
                    step(
                        "prune_forced",
                        "Prune anyway, accepting that a re-transcription would have to re-decode.",
                        command=f"{CLI} prune --session {session_id} --force",
                        required=False,
                    ),
                ],
            )
        if force:
            warnings.append(
                f"WARNING: pruning {session_id} without a session chronicle (--force). Nothing "
                f"downstream has been approved against this transcript yet."
            )
        if dry_run:
            warnings.append(
                f"no session chronicle mentions {session_id} yet — this is an inventory only, "
                f"nothing was deleted, and a real prune is still refused."
            )

    try:
        available = prunable_roles(dataset_dir)
    except DatasetError as exc:
        raise SessionIngestError(
            str(exc), code="dataset_unreadable", detail={"dataset_path": str(dataset_dir)}
        ) from exc

    roles = [role for role in DEFAULT_ROLES if role in available]
    inventory: list[RoleInventory] = []
    for role in available:
        entry = dataset.manifest.file(role)
        target = dataset_dir / entry.path if entry is not None else dataset_dir
        will_prune = role in roles
        inventory.append(
            RoleInventory(
                role=role,
                path=str(target),
                bytes=measure(target),
                pruned=will_prune and not dry_run,
                reason=None
                if will_prune
                else (
                    "regenerating it costs another GPU pass; this CLI has no flag to opt in"
                    if role in DEFERRED_ROLES
                    else "not in the default role set"
                ),
            )
        )

    bytes_freed = 0
    pruned_paths: list[str] = []
    if roles:
        try:
            report = sdk_prune(dataset_dir, roles, dry_run=dry_run)
        except DatasetError as exc:
            raise SessionIngestError(
                str(exc), code="prune_refused", detail={"roles": roles}
            ) from exc
        bytes_freed = report.bytes_freed
        pruned_paths = [str(dataset_dir / rel) for rel in report.paths]
        roles = list(report.roles)

    per_role = {row.role: row.bytes for row in inventory if row.role in roles}
    verb = "would free" if dry_run else "freed"
    lines = [
        f"{'dry-run' if dry_run else 'ok'}: {session_id} — {verb} {human_bytes(bytes_freed)} "
        f"from {dataset_dir}",
    ]
    for row in inventory:
        mark = "prune" if row.role in roles else "keep "
        lines.append(f"  {mark} {row.role:<16} {human_bytes(row.bytes):>10}  {row.path}")
        if row.reason:
            lines.append(f"        ↳ {row.reason}")
    lines.append(
        "  keep  source archive    (never prunable — Craig expires recordings; "
        "it is the only irreplaceable artifact here)"
    )
    lines.append(f"  note: {WSL_NOTE}")
    lines.extend(f"  warning: {message}" for message in warnings)

    return {
        "status": "ok",
        "session": session_id,
        "run": recording.run,
        "recording_id": recording.recording_id,
        "dataset_path": str(dataset_dir),
        "dry_run": dry_run,
        "roles": roles,
        "roles_available": list(available),
        "roles_deferred": [row.role for row in inventory if row.role not in roles],
        "inventory": [row.to_dict() for row in inventory],
        "bytes_freed": bytes_freed,
        "bytes_freed_human": human_bytes(bytes_freed),
        "bytes_by_role": per_role,
        "paths": pruned_paths,
        "chronicles": [str(path) for path in chronicles],
        "note": WSL_NOTE,
        "warnings": warnings,
        "lines": lines,
        "next_steps": next_steps_for("prune", session_id=session_id),
    }
