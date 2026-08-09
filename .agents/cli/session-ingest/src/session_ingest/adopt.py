"""``adopt`` — bind a craig-stt dataset to a session id, or refuse and say why.

Everything here goes through ``craig_stt_dataset``. Parsing ``segments.jsonl`` or
``meta.json`` directly is the violation the producer contract exists to prevent
(CONTRACT rule 1), so this module never opens a dataset file by name.

Four gates, in order, each of which reports rather than decides:

1. **One dataset for the id.** Two candidates is an ambiguity the caller has to
   resolve; picking one would silently bind the wrong recording to a date.
2. **The date matches.** Derived from ``meta.recording.start_time``; a mismatch
   prints both values and exits non-zero. DESIGN §1: *reports mismatches instead
   of choosing.*
3. **The capture is complete.** ``--allow-partial`` is the explicit override.
4. **No track was lost.** craig-stt counts a skipped track in ``tracks`` but not
   in ``tracks_transcribed``, and ``TrackStats.skip_category`` says which kind of
   skip it was. ``"ignored"`` is a decision the run already made — a bot, a
   configured exclusion, or less speech than the VAD floor — so it is adopted
   without ceremony. ``"failed"`` is a speaker this dataset lost: it needs
   ``--allow-skipped-tracks``, which records every skip in ``session.json`` with
   its category instead of swallowing it. A skipped track with *no* category at
   all is refused outright and no flag lifts it — an older craig-stt wrote that
   dataset without recording why the track went, so the fact adopt needs is not
   withheld but absent, and only a re-transcription can supply it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from craig_stt_dataset import (
    MANIFEST_FILENAME,
    Dataset,
    DatasetError,
    known_datasets,
    open_dataset,
)

from .config import SessionConfig
from .errors import DatasetAdoptError
from .models import RecordingLink, SessionLink, SkippedTrack
from .nextsteps import ARCHIVE_PLACEHOLDER, CLI, CRAIG, TRANSCRIBE_JSON_NOTE, next_steps_for, step
from .paths import Roots, SessionTree, session_date_of
from .provenance import CompositeKey, Provenance, as_sha256, downstream_of, utc_now
from .writer import read_json, write_json


@dataclass(slots=True)
class AdoptResult:
    """What adopt did, in the shape the ``--json`` envelope needs."""

    status: str
    session: str
    run: int
    active_run: int
    recording_id: str
    dataset_path: str
    dataset_digest: str
    dataset_status: str
    started_at: str | None
    duration_s: float | None
    session_json: str
    promoted: bool = False
    skipped_tracks: list[dict[str, Any]] = field(default_factory=list)
    invalidated_stages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    next_steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session": self.session,
            "run": self.run,
            "active_run": self.active_run,
            "recording_id": self.recording_id,
            "dataset_path": self.dataset_path,
            "dataset_digest": self.dataset_digest,
            "dataset_status": self.dataset_status,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "session_json": self.session_json,
            "promoted": self.promoted,
            "skipped_tracks": self.skipped_tracks,
            "invalidated_stages": self.invalidated_stages,
            "warnings": self.warnings,
            "checks": self.checks,
            "next_steps": self.next_steps,
        }


def manifest_repair_steps(dataset_dir: Path) -> list[dict[str, Any]]:
    """The documented repair for a dataset written before/without a manifest."""
    return [
        step(
            "describe_dataset",
            (
                "Describe the dataset so it carries a manifest. No re-transcription: "
                "`manifest` only digests what is already on disk."
            ),
            command=f"{CRAIG} manifest {dataset_dir}",
        ),
        step(
            "retry_adopt",
            "Re-run adopt once the manifest exists.",
            command=f"{CLI} adopt {dataset_dir}",
        ),
    ]


def _candidate_roots(roots: Roots) -> list[Path]:
    """Where a dataset for a recording id may live: the work dir, or a scratch run."""
    candidates = [roots.datasets]
    scratch = roots.scratch
    if scratch.is_dir():
        candidates.append(scratch)
        candidates.extend(child for child in sorted(scratch.iterdir()) if child.is_dir())
    return candidates


def find_dataset(target: str, roots: Roots) -> Path:
    """Resolve a dataset directory from a path or a recording id.

    A path is taken at face value. An id is looked up across the work dir and the
    scratch roots; zero or several matches are both errors, because choosing
    between two datasets for one recording is exactly the decision a tool must
    not make on the owner's behalf.
    """
    as_path = Path(target).expanduser()
    if as_path.exists():
        resolved = as_path.resolve()
        return resolved.parent if resolved.is_file() else resolved

    searched = _candidate_roots(roots)
    matches: list[Path] = []
    for root in searched:
        for candidate in known_datasets(root):
            if candidate.name == target:
                matches.append(candidate)
                continue
            try:
                if open_dataset(candidate).recording_id == target:
                    matches.append(candidate)
            except DatasetError:
                continue

    unique = sorted({m.resolve() for m in matches})
    if not unique:
        # A directory that exists but has no manifest is a *repairable* miss, and
        # deserves the repair command rather than "not found".
        bare = roots.datasets / target
        if bare.is_dir():
            raise DatasetAdoptError(
                f"{bare} has no {MANIFEST_FILENAME}, so it cannot be identified or verified.",
                code="missing_manifest",
                next_steps=manifest_repair_steps(bare),
                detail={"dataset_path": str(bare)},
            )
        raise DatasetAdoptError(
            f"no dataset found for {target!r} under: " + ", ".join(str(p) for p in searched),
            code="dataset_not_found",
            detail={"searched": [str(p) for p in searched], "target": target},
        )
    if len(unique) > 1:
        listed = "\n  ".join(str(p) for p in unique)
        raise DatasetAdoptError(
            f"{len(unique)} datasets match {target!r}; pass an explicit path:\n  {listed}",
            code="ambiguous_dataset",
            detail={"candidates": [str(p) for p in unique]},
        )
    return unique[0]


def open_verified(dataset_dir: Path) -> Dataset:
    """``open_dataset(verify=True)`` with the missing-manifest case turned into a repair."""
    try:
        return open_dataset(dataset_dir, verify=True)
    except DatasetError as exc:
        message = str(exc)
        if f"no {MANIFEST_FILENAME}" in message:
            raise DatasetAdoptError(
                message,
                code="missing_manifest",
                next_steps=manifest_repair_steps(dataset_dir),
                detail={"dataset_path": str(dataset_dir)},
            ) from exc
        raise DatasetAdoptError(
            message, code="dataset_unreadable", detail={"dataset_path": str(dataset_dir)}
        ) from exc


def derive_session_date(started_at: datetime | None) -> str | None:
    """The session date implied by the recording's start time.

    Taken as recorded, with no timezone conversion: converting to the machine's
    local zone would make the derived date depend on where the command runs,
    which is not a property a session id may have.
    """
    if started_at is None:
        return None
    return started_at.date().isoformat()


def check_date(session_id: str, derived: str | None) -> tuple[bool, str | None]:
    """A session id must be the derived date, optionally with a same-day suffix."""
    if derived is None:
        return True, "meta.recording.start_time is absent; the session date could not be derived"
    if session_date_of(session_id) == derived:
        return True, None
    return False, None


def load_session_link(tree: SessionTree) -> SessionLink:
    if not tree.session_json.is_file():
        return SessionLink(id=tree.id)
    payload = read_json(tree.session_json)
    if not isinstance(payload, dict):
        raise DatasetAdoptError(
            f"{tree.session_json} does not contain a JSON object", code="session_json_invalid"
        )
    link = SessionLink.from_dict(payload)
    link.id = link.id or tree.id
    return link


#: The one ``skip_category`` that is a decision rather than a loss, and so never
#: needs re-consenting to. craig-stt writes it for a configured exclusion (bot or
#: named user) and for a track whose speech fell below the VAD floor.
DELIBERATE_SKIP_CATEGORY = "ignored"


def retranscribe_steps(*, archive: Path | None, session_id: str) -> list[dict[str, Any]]:
    """The repair for a dataset whose skipped tracks carry no category.

    Not an override: nothing on disk says whether the track was left out or lost,
    so the only way to obtain the fact is to transcribe the recording again with a
    craig-stt that records it. The LOCAL archive is what gets read — Craig expires
    recordings, so the zip already on disk is the irreplaceable copy.
    """
    source = str(archive) if archive is not None else ARCHIVE_PLACEHOLDER
    return [
        step(
            "craig_transcribe_current",
            (
                "Re-transcribe from the LOCAL archive with the current craig-stt, which "
                "records why each track was skipped. No re-download. " + TRANSCRIBE_JSON_NOTE
            ),
            command=f"{CRAIG} transcribe {source} --json",
        ),
        step(
            "retry_adopt",
            "Adopt the dataset the current craig-stt wrote.",
            command=f"{CLI} adopt <dataset> --session {session_id}",
        ),
    ]


def _skipped_track_rows(dataset: Dataset) -> list[dict[str, Any]]:
    """Every track the dataset did not transcribe, with the SDK's own reason for it.

    ``skip_category`` and ``deliberately_ignored`` are two different facts and
    both are kept: the category is per-track and covers the VAD floor as well as
    configuration, while ``provenance.ignored_tracks`` records what the *run was
    asked* to leave out. A track can be ``"ignored"`` without appearing there.
    """
    return [
        {
            "track": track.track,
            "user_id": track.user_id,
            "username": track.username,
            "skip_category": track.skip_category,
            "skip_reason": track.skip_reason,
            "deliberately_ignored": bool(
                dataset.meta.provenance.ignored_tracks
                and track.track in dataset.meta.provenance.ignored_tracks
            ),
        }
        for track in dataset.meta.tracks
        if track.skipped
    ]


def uncategorised_skips(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The skipped tracks whose dataset never recorded *why* they were skipped.

    Their category is missing, not withheld, so there is nothing for the operator
    to consent to: an older craig-stt wrote free text where the category belongs,
    and a deliberate omission and a lost speaker are the same bytes there.
    """
    return [dict(row) for row in rows if not row.get("skip_category")]


def blocking_skips(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The skipped tracks that still need the operator to say yes.

    Everything craig-stt categorised as ``"ignored"`` is filtered out: the
    exclusion was already decided, at transcription time, by the configuration
    the run was given. What is left is a categorised skip that may not be assumed
    deliberate — ``"failed"``, a speaker this dataset lost. Rows with no category
    at all are not here: they are ``uncategorised_skips``, which no flag accepts.
    """
    return [
        dict(row)
        for row in rows
        if row.get("skip_category") and row["skip_category"] != DELIBERATE_SKIP_CATEGORY
    ]


def _describe_skip(row: Mapping[str, Any]) -> str:
    who = row["username"] or row["user_id"] or "unknown"
    category = row.get("skip_category") or "no skip_category"
    return (
        f"track {row['track']} ({who}): {category} — "
        f"{row['skip_reason'] or 'no reason recorded'}"
        + (" [deliberately ignored]" if row["deliberately_ignored"] else "")
    )


def run_adopt(
    *,
    target: str,
    roots: Roots,
    config: SessionConfig,
    session_id: str | None = None,
    run: int | None = None,
    promote: bool = False,
    allow_partial: bool = False,
    allow_skipped_tracks: bool = False,
    force_relink: bool = False,
    force: bool = False,
    on_resolved: Callable[[str, str, int], None] | None = None,
) -> AdoptResult:
    """Adopt one dataset into one session.

    ``on_resolved(session_id, dataset_digest, run)`` fires as soon as the manifest
    has been read and before the (expensive) full verification, so the caller can
    print its status line "before work" with a real digest in it.
    """
    dataset_dir = find_dataset(target, roots)

    # Cheap manifest read first: it gives the digest for the status line without
    # re-hashing gigabytes.
    preview = open_verified_manifest(dataset_dir)
    digest = as_sha256(preview.digest) or preview.digest
    recording_id = preview.recording_id

    started_at = preview.meta.recording.start_time
    derived_date = derive_session_date(started_at)
    effective_session = session_id or derived_date
    if effective_session is None:
        raise DatasetAdoptError(
            f"{dataset_dir} records no meta.recording.start_time, so the session date cannot "
            f"be derived. Pass --session YYYY-MM-DD explicitly.",
            code="session_undeterminable",
            detail={"dataset_path": str(dataset_dir)},
        )

    tree = roots.session(effective_session)
    link = load_session_link(tree)
    effective_run = run if run is not None else (link.active_run if link.recordings else 1)
    if effective_run < 1:
        raise DatasetAdoptError("--run must be >= 1", code="invalid_run")

    if on_resolved is not None:
        on_resolved(effective_session, digest, effective_run)

    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    # --- gate 2: the date -----------------------------------------------------
    ok, note = check_date(effective_session, derived_date)
    checks.append(
        {
            "check": "session_date",
            "ok": ok,
            "session": effective_session,
            "derived_from_start_time": derived_date,
        }
    )
    if note:
        warnings.append(note)
    if not ok:
        raise DatasetAdoptError(
            f"session date mismatch: --session says {effective_session!r} "
            f"(date part {session_date_of(effective_session)!r}) but "
            f"meta.recording.start_time says {derived_date!r}. Both are reported; "
            f"adopt will not choose between them.",
            code="date_mismatch",
            detail={
                "session": effective_session,
                "derived_from_start_time": derived_date,
                "start_time": started_at.isoformat() if started_at else None,
                "dataset_path": str(dataset_dir),
            },
        )

    # --- full verification ----------------------------------------------------
    dataset = open_verified(dataset_dir)

    # --- gate 3: complete -----------------------------------------------------
    checks.append(
        {"check": "status_complete", "ok": dataset.status == "complete", "status": dataset.status}
    )

    def override_step(step_id: str, summary: str, flag: str) -> list[dict[str, Any]]:
        """The same adopt invocation plus the override flag this gate documents.

        A gate whose whole message is "pass --X to accept this" is exactly the
        agent-fixable failure `next_steps` exists for (see errors.SessionIngestError);
        leaving it empty makes an agent walking the chain reconstruct the command
        from prose. `required=False` because accepting the finding is the operator's
        call, not an automatic repair.
        """
        return [
            step(
                step_id,
                summary,
                command=(
                    f"{CLI} adopt {target} --session {effective_session}"
                    + (f" --run {effective_run}" if effective_run != 1 else "")
                    + f" {flag}"
                ),
                required=False,
            )
        ]

    if dataset.status != "complete" and not allow_partial:
        raise DatasetAdoptError(
            f"{dataset_dir} is a {dataset.status} capture (the recording was still running "
            f"when it was fetched). Re-fetch it, or pass --allow-partial to adopt it anyway.",
            code="partial_dataset",
            detail={"dataset_path": str(dataset_dir), "status": dataset.status},
            next_steps=override_step(
                "adopt_allow_partial",
                "Adopt the partial capture anyway; the status is recorded in session.json.",
                "--allow-partial",
            ),
        )
    if dataset.status != "complete":
        warnings.append(f"adopted a {dataset.status} capture (--allow-partial)")

    # --- gate 4: no track was lost --------------------------------------------
    counts = dataset.meta.counts
    skipped = _skipped_track_rows(dataset)
    uncategorised = uncategorised_skips(skipped)
    blocking = blocking_skips(skipped)
    # The arithmetic is still consulted, because it catches what the track list
    # cannot say: a shortfall with no TrackStats row behind it is a track that
    # vanished from meta.json entirely, and no category can describe it.
    unaccounted = (counts.tracks - counts.tracks_transcribed) - len(skipped)
    tracks_ok = not blocking and not uncategorised and unaccounted <= 0
    checks.append(
        {
            "check": "tracks_transcribed",
            "ok": tracks_ok,
            "tracks": counts.tracks,
            "tracks_transcribed": counts.tracks_transcribed,
            "skipped": skipped,
            "blocking": blocking,
            "uncategorised": uncategorised,
            "unaccounted": max(unaccounted, 0),
        }
    )
    if uncategorised:
        # Checked before the flag, because the flag cannot answer this one. The
        # dataset is a regenerable artifact; re-transcribing it is the repair.
        listed = "\n  ".join(_describe_skip(row) for row in uncategorised)
        archive = local_archive(dataset_dir, dataset)
        raise DatasetAdoptError(
            f"{counts.tracks_transcribed}/{counts.tracks} tracks were transcribed, and these "
            f"skips carry no skip_category:\n  {listed}\n"
            f"The dataset was written by an older craig-stt that recorded no category for a "
            f"skipped track, so a deliberate omission and a lost speaker are the same bytes "
            f"and nothing on disk tells them apart. --allow-skipped-tracks does not help: the "
            f"fact is missing, not withheld. Re-transcribe the recording from its local "
            f"archive with the current craig-stt and adopt that dataset instead.",
            code="tracks_missing_skip_category",
            detail={
                "tracks": counts.tracks,
                "tracks_transcribed": counts.tracks_transcribed,
                "skipped": skipped,
                "uncategorised": uncategorised,
                "archive": str(archive) if archive is not None else None,
                "dataset_path": str(dataset_dir),
            },
            next_steps=retranscribe_steps(archive=archive, session_id=effective_session),
        )
    if not tracks_ok and not allow_skipped_tracks:
        listed = "\n  ".join(_describe_skip(row) for row in blocking)
        if unaccounted > 0:
            listed += f"\n  {unaccounted} further track(s) short, with no entry in meta.tracks"
        raise DatasetAdoptError(
            f"{counts.tracks_transcribed}/{counts.tracks} tracks were transcribed, and these "
            f"skips are not accounted for as deliberate:\n  {listed}\n"
            f"A skip_category of {DELIBERATE_SKIP_CATEGORY!r} is adopted without a flag — the "
            f"run was configured to leave that track out, or it held less speech than the VAD "
            f"floor. 'failed' means the speaker was lost and may not be assumed deliberate. "
            f"Re-run with --allow-skipped-tracks to accept them; every skip is then recorded "
            f"in session.json with its category.",
            code="tracks_not_transcribed",
            detail={
                "tracks": counts.tracks,
                "tracks_transcribed": counts.tracks_transcribed,
                "skipped": skipped,
                "blocking": blocking,
                "uncategorised": uncategorised,
                "unaccounted": max(unaccounted, 0),
                "dataset_path": str(dataset_dir),
            },
            next_steps=override_step(
                "adopt_allow_skipped_tracks",
                "Adopt anyway if the skips are intended; each one is recorded in session.json.",
                "--allow-skipped-tracks",
            ),
        )
    if not tracks_ok:
        warnings.append(
            f"{len(blocking) + max(unaccounted, 0)} track(s) were not transcribed and were "
            f"adopted anyway (--allow-skipped-tracks)"
        )
    deliberate = len(skipped) - len(blocking)
    if deliberate:
        warnings.append(
            f"{deliberate} track(s) were deliberately ignored by craig-stt "
            f"(skip_category={DELIBERATE_SKIP_CATEGORY}); no flag is needed for those"
        )

    # --- promotion ------------------------------------------------------------
    target_active_run = effective_run if promote else link.active_run
    if not link.recordings:
        target_active_run = effective_run
    if promote:
        chronicles = tree.chronicle_candidates()
        if chronicles and not force_relink:
            listed = ", ".join(str(p) for p in chronicles)
            raise DatasetAdoptError(
                f"a session chronicle already exists for {effective_session} ({listed}). "
                f"A re-transcription renumbers every segment and turn ID, so promoting would "
                f"silently break its approved evidence links. Pass --force-relink to override "
                f"and then re-link the chronicle.",
                code="chronicle_exists",
                detail={"chronicles": [str(p) for p in chronicles]},
                next_steps=[
                    step(
                        "relink_chronicle",
                        "Re-link the chronicle's evidence after a forced promotion.",
                        command=(
                            f"{CLI} adopt {target} --session {effective_session} "
                            f"--run {effective_run} --promote --force-relink"
                        ),
                        required=False,
                    )
                ],
            )
        if chronicles:
            warnings.append(
                f"promoted over an existing chronicle (--force-relink): "
                f"{', '.join(str(p) for p in chronicles)}; its evidence links now need re-linking"
            )

    # --- skip-if-done ---------------------------------------------------------
    provenance = Provenance.load(tree.provenance_json)
    key = CompositeKey(
        dataset_digest=digest,
        knobs={
            "run": effective_run,
            "recording_id": recording_id,
            "active_run": target_active_run,
            "session": effective_session,
        },
    )
    digest_before = link.dataset_digest

    if provenance.should_skip("adopt", key, force=force, root=tree.root):
        return AdoptResult(
            status="skipped",
            session=effective_session,
            run=effective_run,
            active_run=link.active_run,
            recording_id=recording_id,
            dataset_path=str(dataset_dir),
            dataset_digest=digest,
            dataset_status=dataset.status,
            started_at=started_at.isoformat() if started_at else None,
            duration_s=dataset.meta.recording.duration_s,
            session_json=str(tree.session_json),
            promoted=False,
            skipped_tracks=skipped,
            warnings=[
                *warnings,
                "already adopted with this dataset digest; use --force to rewrite",
            ],
            checks=checks,
            next_steps=next_steps_for(
                "adopt",
                session_id=effective_session,
                api_key_present=config.api_key_present,
                run=effective_run,
            ),
        )

    # --- write ----------------------------------------------------------------
    link.upsert(
        RecordingLink(
            recording_id=recording_id,
            dataset_path=str(dataset_dir),
            run=effective_run,
            dataset_digest=digest,
            status=dataset.status,
            started_at=started_at.isoformat() if started_at else None,
            duration_s=dataset.meta.recording.duration_s,
            adopted_at=utc_now(),
            skipped_tracks=tuple(SkippedTrack.from_dict(row) for row in skipped),
        )
    )
    link.active_run = target_active_run
    link.updated_at = utc_now()
    write_json(tree.session_json, link.to_dict())
    tree.run_dir(effective_run).mkdir(parents=True, exist_ok=True)

    invalidated: list[str] = []
    if link.dataset_digest != digest_before:
        # The active digest moved, so every downstream artifact was produced
        # against a transcript that no longer exists. Their composite keys would
        # mismatch anyway; dropping the records makes that explicit and visible.
        invalidated = provenance.invalidate(downstream_of("adopt"), save=False)

    provenance.mark_done(
        "adopt",
        key,
        outputs=[tree.session_json],
        extra={
            "dataset_path": str(dataset_dir),
            "recording_id": recording_id,
            "run": effective_run,
            "active_run": link.active_run,
            "dataset_status": dataset.status,
            "invalidated": invalidated,
        },
    )

    return AdoptResult(
        status="ok",
        session=effective_session,
        run=effective_run,
        active_run=link.active_run,
        recording_id=recording_id,
        dataset_path=str(dataset_dir),
        dataset_digest=digest,
        dataset_status=dataset.status,
        started_at=started_at.isoformat() if started_at else None,
        duration_s=dataset.meta.recording.duration_s,
        session_json=str(tree.session_json),
        promoted=promote,
        skipped_tracks=skipped,
        invalidated_stages=invalidated,
        warnings=warnings,
        checks=checks,
        next_steps=next_steps_for(
            "adopt",
            session_id=effective_session,
            api_key_present=config.api_key_present,
            run=effective_run,
        ),
    )


def open_verified_manifest(dataset_dir: Path) -> Dataset:
    """Open without re-digesting: enough for the manifest facts the status line needs."""
    try:
        return open_dataset(dataset_dir, verify=False)
    except DatasetError as exc:
        message = str(exc)
        if f"no {MANIFEST_FILENAME}" in message:
            raise DatasetAdoptError(
                message,
                code="missing_manifest",
                next_steps=manifest_repair_steps(dataset_dir),
                detail={"dataset_path": str(dataset_dir)},
            ) from exc
        raise DatasetAdoptError(
            message, code="dataset_unreadable", detail={"dataset_path": str(dataset_dir)}
        ) from exc


def resolve_active_dataset(
    tree: SessionTree, run: int | None = None
) -> tuple[RecordingLink, Dataset]:
    """Open the dataset backing a session run — the read path every later verb uses."""
    link = load_session_link(tree)
    if not link.recordings:
        raise DatasetAdoptError(
            f"{tree.id} has no adopted recording. Run `{CLI} adopt <dataset|recording-id> "
            f"--session {tree.id}` first.",
            code="not_adopted",
            detail={"session": tree.id},
            next_steps=[
                step(
                    "adopt",
                    "Adopt the dataset for this session.",
                    command=f"{CLI} adopt <dataset|recording-id> --session {tree.id}",
                )
            ],
        )
    effective_run = run if run is not None else link.active_run
    recordings: Sequence[RecordingLink] = link.for_run(effective_run)
    if not recordings:
        raise DatasetAdoptError(
            f"{tree.id} has no recording adopted for run {effective_run} "
            f"(known runs: {link.runs()}).",
            code="run_not_adopted",
            detail={"session": tree.id, "run": effective_run, "known_runs": link.runs()},
        )
    if len(recordings) > 1:
        raise DatasetAdoptError(
            f"{tree.id} run {effective_run} has {len(recordings)} recordings; multi-recording "
            f"sessions are representable in session.json but not yet aggregated by this verb.",
            code="multi_recording_unsupported",
            detail={"recordings": [r.recording_id for r in recordings]},
        )
    recording = recordings[0]
    return recording, open_verified(Path(recording.dataset_path))


def local_archive(dataset_dir: Path, dataset: Dataset) -> Path | None:
    """The local ``.flac.zip`` a re-transcription reads, or ``None``.

    A re-run must never re-download: Craig expires recordings, so the archive
    already on disk is the irreplaceable copy. The manifest's ``audio_archive``
    role is authoritative; the ``source/*.zip`` glob is the fallback for a dataset
    described before that role existed.

    Lives here rather than in ``plan`` because ``qa`` emits the same re-run
    command from the threshold-crossed branch, and a step an agent is told to run
    verbatim must not contain a placeholder when the real path is knowable.
    """
    entry = dataset.manifest.file("audio_archive")
    if entry is not None and (dataset_dir / entry.path).is_file():
        return dataset_dir / entry.path
    archives = sorted((dataset_dir / "source").glob("*.zip"))
    return archives[0] if archives else None
