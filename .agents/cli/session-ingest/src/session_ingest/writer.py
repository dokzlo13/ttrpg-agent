"""Atomic writes for the small JSON sidecars.

Every artifact under ``.cache/sessions/<id>/`` is read by a later verb that
trusts what it finds. A torn ``session.json`` or ``qa.json`` would be trusted
just the same, so writes go out through a temp file and one ``os.replace``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_json(path: Path, payload: Any) -> Path:
    return write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
