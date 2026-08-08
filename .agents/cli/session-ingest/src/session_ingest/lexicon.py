"""``lexicon`` — read-only inspection of ``_lexicon.yaml`` and its generated forms.

The audit path for ``morph: true``. Everything ``render`` will substitute and
everything ``qa`` will count can be printed before either runs, together with
the reason for each string that produced nothing — which is what keeps
mechanical expansion honest: a generated pair nobody can see is a guess, and a
generated pair anyone can list is a lookup.

Writes nothing, touches no session, and needs no dataset. It is safe to run
against the live vault at any time, including while a transcription is going.
"""

from __future__ import annotations

from typing import Any

from .morphs import ExpansionTable, empty_table, expand_terms
from .paths import Roots
from .vaultfiles import Lexicon, load_lexicon


def term_rows(lexicon: Lexicon) -> list[dict[str, Any]]:
    """One row per term, in file order — the order priority ties resolve in."""
    return [term.to_dict() for term in lexicon.terms]


def expansion_rows(lexicon: Lexicon, expansion: ExpansionTable) -> list[dict[str, Any]]:
    """Per-term generated forms, including terms that opted in and got nothing."""
    rows: list[dict[str, Any]] = []
    for term in lexicon.terms:
        if not term.morph:
            continue
        generated = expansion.terms.get(term.id)
        rows.append(
            {
                "term_id": term.id,
                "lemma": term.display_ru or term.canonical,
                "active": term.active,
                "display_forms": list(generated.display_forms) if generated else [],
                "pairs": [pair.to_dict() for pair in generated.pairs] if generated else [],
                "notes": [note.to_dict() for note in expansion.notes if note.term_id == term.id],
            }
        )
    return rows


def _human_lines(lexicon: Lexicon, expansion: ExpansionTable, *, expand: bool) -> list[str]:
    lines = [
        f"{'ok' if lexicon.present else 'absent'}: {lexicon.path}",
        f"  terms: {len(lexicon.terms)} ({len(lexicon.active_terms())} active, "
        f"{sum(1 for t in lexicon.terms if t.morph)} with morph: true)",
    ]
    for term in lexicon.terms:
        flags = [term.kind or "—", f"priority {term.priority}"]
        if not term.active:
            flags.append("inactive")
        if term.morph:
            flags.append("morph")
        lines.append(
            f"  {term.id}: {term.canonical} → {term.display_ru or term.canonical} "
            f"[{', '.join(flags)}]"
        )
        if term.variants:
            lines.append(f"      variants: {', '.join(term.variants)}")
    if not expand:
        return lines

    versions = expansion.versions
    lines.extend(
        [
            "",
            f"  Expansion (pymorphy3 {versions.pymorphy3}, "
            f"dicts-ru {versions.dicts_ru}) digest {expansion.digest()}",
        ]
    )
    for row in expansion_rows(lexicon, expansion):
        lines.append(f"  {row['term_id']} ({row['lemma']}):")
        forms = row["display_forms"]
        lines.append(f"      display forms: {', '.join(forms) if forms else '—'}")
        for pair in row["pairs"]:
            lines.append(f"      {pair['case']}: {pair['variant']} → {pair['display']}")
        if not row["pairs"]:
            lines.append("      pairs: —")
        for note in row["notes"]:
            lines.append(f"      {note['kind']}: {note['subject']} — {note['reason']}")
    return lines


def run(*, roots: Roots, expand: bool = False) -> dict[str, Any]:
    """Returns the ``--json`` payload: the loaded terms, optionally expanded."""
    lexicon = load_lexicon(roots.lexicon_file)
    expansion = expand_terms(lexicon) if expand else empty_table(lexicon)

    payload: dict[str, Any] = {
        "status": "ok" if lexicon.present else "absent",
        "path": str(lexicon.path),
        "present": lexicon.present,
        "digest": lexicon.digest,
        "terms": term_rows(lexicon),
        "term_count": len(lexicon.terms),
        "active_term_count": len(lexicon.active_terms()),
        "morph_term_count": sum(1 for term in lexicon.terms if term.morph),
        "expanded": expand,
    }
    if expand:
        payload["expansion"] = expansion.to_dict()
        payload["expansion_by_term"] = expansion_rows(lexicon, expansion)
    payload["warnings"] = (
        [] if lexicon.present else [f"{lexicon.path} does not exist; there is nothing to inspect"]
    )
    payload["lines"] = _human_lines(lexicon, expansion, expand=expand)
    payload["next_steps"] = []
    return payload
