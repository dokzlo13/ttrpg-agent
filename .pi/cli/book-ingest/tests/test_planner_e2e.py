import json

from book_ingest.planner import _candidates_from_marker_filtered, _choose_split_levels


def _node(block_type, **kw):
    return {"block_type": block_type, **kw}


def _section_header(html, page_block_id):
    return _node(
        "SectionHeader",
        html=html,
        id=f"{page_block_id}/SectionHeader/{abs(hash(html)) % 1000}",
    )


def _page(idx, headers):
    return _node(
        "Page",
        id=f"/page/{idx}/Page/{idx}",
        children=headers,
    )


def _document(pages):
    return {"block_type": "Document", "children": pages}


def test_choose_split_levels_prefers_numbered():
    from book_ingest.planner import _RawCandidate

    headers = [
        _RawCandidate("1 Intro", 0, 2, "marker-json"),
        _RawCandidate("Sub heading", 0, 3, "marker-json"),
        _RawCandidate("2 Finding", 1, 1, "marker-json"),
        _RawCandidate("Sub two", 1, 3, "marker-json"),
        _RawCandidate("3 Padduck", 2, 1, "marker-json"),
    ]
    levels, numbered_only = _choose_split_levels(headers)
    assert numbered_only is True
    assert levels == {1, 2}  # both are accepted as splitters when numbered


def test_choose_split_levels_falls_back_to_h1():
    from book_ingest.planner import _RawCandidate

    headers = [
        _RawCandidate("First", 0, 1, "marker-json"),
        _RawCandidate("Second", 1, 1, "marker-json"),
        _RawCandidate("Sub", 1, 3, "marker-json"),
    ]
    levels, numbered_only = _choose_split_levels(headers)
    assert numbered_only is False
    assert levels == {1}


def test_marker_json_candidates_filter(tmp_path):
    doc = _document(
        [
            _page(0, [_section_header("<h2>1 Intro</h2>", "/page/0")]),
            _page(1, [_section_header("<h1>2 Finding</h1>", "/page/1")]),
            _page(1, [_section_header("<h3>Subheading</h3>", "/page/1")]),
            _page(2, [_section_header("<h1>3 Padduck</h1>", "/page/2")]),
        ]
    )
    json_path = tmp_path / "doc.json"
    json_path.write_text(json.dumps(doc))
    candidates = _candidates_from_marker_filtered(json_path)
    titles = [c.title for c in candidates]
    assert titles == ["1 Intro", "2 Finding", "3 Padduck"]
    # Subheading filtered out because numbered-prefix-only mode is active
    assert all("Subheading" not in t for t in titles)


def test_marker_json_candidates_handles_h1_only(tmp_path):
    doc = _document(
        [
            _page(0, [_section_header("<h1>Foreword</h1>", "/page/0")]),
            _page(2, [_section_header("<h1>Chapter One</h1>", "/page/2")]),
            _page(2, [_section_header("<h2>A subsection</h2>", "/page/2")]),
        ]
    )
    json_path = tmp_path / "doc.json"
    json_path.write_text(json.dumps(doc))
    candidates = _candidates_from_marker_filtered(json_path)
    titles = [c.title for c in candidates]
    assert titles == ["Foreword", "Chapter One"]
