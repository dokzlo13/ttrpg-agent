"""Test doubles for the metered stages.

Every LLM interaction in this suite goes through :class:`FakeClient`, which is
shaped exactly like the slice of the OpenAI client the code touches
(``client.chat.completions.create(...) -> response.choices[0].message.content``
plus ``response.usage``). Going through the real plumbing — schema checking,
the repair retry, the thread pool, usage accumulation — is the point: a test
that monkeypatched ``structured_call`` would assert nothing about any of it.

Also here: the fixture builders for the two artifacts wave 2's ``render`` writes
and this half of the pipeline reads (``anchors.json``) or produces
(``extraction.json``), so the metered tests do not wait on another module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from session_ingest.adopt import run_adopt
from session_ingest.config import SessionConfig, resolve_config
from session_ingest.writer import write_json

from .conftest import SEGMENT_COUNT, Workspace

TURN_ID_RE = re.compile(r"\((t\d+-\d+)\)")


# ------------------------------------------------------------- fake client


class _Usage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class FakeResponse:
    def __init__(self, content: str, *, prompt_tokens: int = 10, completion_tokens: int = 5):
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt_tokens, completion_tokens)


class _Completions:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> FakeResponse:
        return self._client.dispatch(kwargs)


class _Chat:
    def __init__(self, client: FakeClient) -> None:
        self.completions = _Completions(client)


class FakeClient:
    """``handler(kwargs, call_index)`` returns a dict, a raw string, or an exception."""

    def __init__(self, handler: Callable[[dict[str, Any], int], Any]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.chat = _Chat(self)

    def dispatch(self, kwargs: dict[str, Any]) -> FakeResponse:
        index = len(self.calls)
        self.calls.append(kwargs)
        outcome = self.handler(kwargs, index)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeResponse):
            return outcome
        if isinstance(outcome, str):
            return FakeResponse(outcome)
        return FakeResponse(json.dumps(outcome, ensure_ascii=False))

    # -- introspection helpers -------------------------------------------

    def user_contents(self) -> list[str]:
        return [str(call["messages"][1]["content"]) for call in self.calls]

    def system_prompts(self) -> list[str]:
        return [str(call["messages"][0]["content"]) for call in self.calls]

    def schemas(self) -> list[dict[str, Any]]:
        return [call["response_format"]["json_schema"] for call in self.calls]


def user_turn_ids(kwargs: Mapping[str, Any]) -> list[str]:
    """The turn ids the window under this call actually contained."""
    return TURN_ID_RE.findall(str(kwargs["messages"][1]["content"]))


def constant(payload: Any) -> Callable[[dict[str, Any], int], Any]:
    return lambda _kwargs, _index: payload


def sequence(*payloads: Any) -> Callable[[dict[str, Any], int], Any]:
    """One payload per call, in order; the last one repeats."""

    def handler(_kwargs: dict[str, Any], index: int) -> Any:
        return payloads[min(index, len(payloads) - 1)]

    return handler


def classify_all(
    *,
    default: str = "in_character",
    by_turn: Mapping[str, str] | None = None,
    confidence: float = 0.9,
) -> Callable[[dict[str, Any], int], Any]:
    """A ``segment`` handler that labels every turn it was shown."""
    overrides = dict(by_turn or {})

    def handler(kwargs: dict[str, Any], _index: int) -> Any:
        return {
            "turns": [
                {
                    "turn_id": turn_id,
                    "class": overrides.get(turn_id, default),
                    "confidence": confidence,
                }
                for turn_id in user_turn_ids(kwargs)
            ]
        }

    return handler


EMPTY_EXTRACTION: dict[str, list[Any]] = {
    "scenes": [],
    "events": [],
    "entities": [],
    "quests": [],
    "loot": [],
    "commitments": [],
    "threads": [],
}


def extraction_answer(**families: Any) -> dict[str, Any]:
    """One window's ``extract`` answer with every family present (strict schema)."""
    payload = {family: list(rows) for family, rows in EMPTY_EXTRACTION.items()}
    payload.update({family: list(rows) for family, rows in families.items()})
    return payload


# --------------------------------------------------------------- fixtures


def turn_id_for(segment_index: int) -> str:
    """Mirrors the SDK formula for the conftest dataset (one segment per turn)."""
    track = 1 if segment_index % 2 == 0 else 2
    return f"t{round(segment_index * 10 * 100)}-{track}"


def chunk_for(segment_index: int, *, per_chunk: int = 15) -> str:
    number = segment_index // per_chunk + 1
    start = (segment_index // per_chunk) * per_chunk * 10 // 60
    end = start + per_chunk * 10 // 60
    return f"{number:02d}-{start:03d}-{end:03d}"


def write_anchors(workspace: Workspace, *, segments: int = SEGMENT_COUNT) -> Path:
    """``render``'s ID bridge, in the format wave 2's render emits."""
    tree = workspace.roots.session(workspace.session_id)
    payload = {
        str(index): {
            "turn_id": turn_id_for(index),
            "chunk": chunk_for(index),
            "t0": float(index * 10),
        }
        for index in range(segments)
    }
    return write_json(tree.anchors_json, payload)


def evidence(*segment_indices: int, speaker: str = "Морвика") -> list[dict[str, Any]]:
    return [
        {
            "turn_id": turn_id_for(index),
            "t0": float(index * 10),
            "speaker": speaker,
            "segment_indices": [index],
        }
        for index in segment_indices
    ]


def default_elements() -> dict[str, list[dict[str, Any]]]:
    """One element per family, each citing turns that exist in the fixture dataset."""
    return {
        "scenes": [
            {
                "id": "s1",
                "title": "Старая часовня",
                "location": "Кроненфельд",
                "summary": "Партия обыскала старую часовню.",
                "participants": ["Морвика"],
                "t0": 0.0,
                "t1": 40.0,
                "confidence": 0.8,
                "evidence": evidence(0, 2),
            }
        ],
        "events": [
            {
                "id": "e1",
                "scene": "s1",
                "kind": "discovery",
                "summary": "Найден алтарь с амулетом Вазгара",
                "outcome": "Партия забрала амулет",
                "world_impact": "local",
                "needs_owner": False,
                "confidence": 0.7,
                "evidence": evidence(2),
            },
            {
                "id": "e2",
                "scene": None,
                "kind": "meta",
                "summary": "Игроки поспорили о правилах",
                "outcome": None,
                "world_impact": "none",
                "needs_owner": True,
                "confidence": 0.4,
                "evidence": evidence(4),
            },
        ],
        "entities": [
            {
                "id": "n1",
                "kind": "npc",
                "name_as_heard": "Вагзар",
                "canonical": "Вазгар",
                "first_mention_t": 20.0,
                "confidence": 0.9,
                "evidence": evidence(2),
            }
        ],
        "quests": [
            {
                "id": "q1",
                "name": "Найти амулет",
                "status_change": "advanced",
                "detail": "Амулет найден в часовне",
                "confidence": 0.6,
                "evidence": evidence(2),
            }
        ],
        "loot": [
            {
                "item": "Медный амулет",
                "recipient": "Морвика",
                "quantity": "1",
                "confidence": 0.5,
                "evidence": evidence(2),
            }
        ],
        "commitments": [
            {
                "who": "Морвика",
                "promise": "Вернуть амулет жрецу",
                "deadline": None,
                "confidence": 0.5,
                "evidence": evidence(4),
            }
        ],
        "threads": [
            {
                "question": "Кто разорил часовню?",
                "status": "open",
                "confidence": 0.5,
                "evidence": evidence(6),
            }
        ],
    }


def write_extraction(
    workspace: Workspace,
    *,
    elements: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    dataset_digest: str | None = None,
    **extra: Any,
) -> Path:
    tree = workspace.roots.session(workspace.session_id)
    payload: dict[str, Any] = {
        "schema": "ttrpg.session-extraction/1",
        "session": workspace.session_id,
        "run": 1,
        "generated_at": "2026-08-08T20:00:00Z",
        "dataset_digest": dataset_digest or "sha256:" + "0" * 64,
        "lexicon_digest": None,
        "prompt_version": "extract/1",
        "model": "gpt-test",
        "merge_gap_s": 1.5,
        "windows": 1,
        "elements": (
            {family: [dict(row) for row in rows] for family, rows in elements.items()}
            if elements is not None
            else default_elements()
        ),
        "failed_windows": [],
        "rejected_elements": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "calls": 1},
    }
    payload.update(extra)
    return write_json(tree.extraction_json, payload)


def write_classes(workspace: Workspace, classes: Mapping[str, str]) -> Path:
    tree = workspace.roots.session(workspace.session_id)
    tree.root.mkdir(parents=True, exist_ok=True)
    tree.turns_class_jsonl.write_text(
        "".join(
            json.dumps({"turn_id": turn_id, "class": value, "confidence": 0.9}, ensure_ascii=False)
            + "\n"
            for turn_id, value in classes.items()
        ),
        encoding="utf-8",
    )
    return tree.turns_class_jsonl


# ------------------------------------------------------------------ config


def make_config(*, api_key: str | None = "sk-test-not-a-real-key", **env: str) -> SessionConfig:
    """A resolved config with concurrency pinned to 1 so call order is deterministic."""
    environment: dict[str, str] = {"TTRPG_SESSION_OPENAI_MAX_CONCURRENCY": "1"}
    if api_key:
        environment["OPENAI_API_KEY"] = api_key
    environment.update(env)
    return resolve_config(env=environment)


def adopt(workspace: Workspace) -> None:
    """Bind the fixture dataset to the session so the read path resolves."""
    run_adopt(
        target=str(workspace.dataset_dir),
        roots=workspace.roots,
        config=make_config(api_key=None),
        allow_skipped_tracks=True,
    )
