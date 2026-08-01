from pathlib import Path

import pytest

from book_ingest.planner import (
    book_title_from,
    clean_title,
    is_noise_title,
    looks_like_ocr_noise,
    slugify,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("THE APPROACH", "The Approach"),
        ("<h1>1 Introduction &amp; overview</h1>", "1 Introduction & overview"),
        ("· THE GRAVEYARD ·", "The Graveyard"),
        ("Player Handout #1", "Player Handout #1"),
        ("[link text](http://x)", "link text"),
        ("{4}--------", ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Front Cover", True),
        ("Title", True),
        ("Endpaper", True),
        ("Contents", True),
        ("Table of Contents", True),
        ("Copyright Rob Alexander 2019-2021", True),
        ("Copyright info", True),
        ("The Approach", False),
        ("THE SHRINE", False),
    ],
)
def test_is_noise_title(title, expected):
    assert is_noise_title(title.lower()) is expected
    assert is_noise_title(title) is expected


def test_is_noise_title_matches_book_title():
    assert is_noise_title("Death Frost Doom", book_title="Death Frost Doom") is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AAA", True),
        ("XYZ", True),
        ("a", True),
        ("", True),
        ("abc", False),
        ("Hello", False),
        ("A1", True),
    ],
)
def test_looks_like_ocr_noise(text, expected):
    assert looks_like_ocr_noise(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The Approach", "the-approach"),
        ("· THE GRAVEYARD ·", "the-graveyard"),
        ("Player Handout #1", "player-handout-1"),
        ("Map: The Shrine", "map-the-shrine"),
        ("", "section"),
        ("   ", "section"),
    ],
)
def test_slugify(text, expected):
    assert slugify(text) == expected


@pytest.mark.parametrize(
    ("stem", "metadata", "expected"),
    [
        ("LotFP - Death Frost Doom v2-1", {}, "LotFP - Death Frost Doom"),
        ("The Pit in the Forest [v1.2] (Basic,BX)", {}, "The Pit in the Forest"),
        ("Phb 2024", {"/Title": "Player's Handbook (2024)"}, "Player's Handbook (2024)"),
        ("noversion", {}, "noversion"),
        ("Some Book", {"/Title": "  "}, "Some Book"),
    ],
)
def test_book_title_from(stem, metadata, expected):
    pdf = Path(f"/tmp/{stem}.pdf")
    assert book_title_from(pdf, metadata) == expected
