---
name: ttrpg-import-raw-pdf
description: |
  Direct invocation of marker for PDF→markdown conversion. Only use when
  ttrpg-import-book-pdf is the wrong tool — e.g. user wants a one-off conversion
  of a non-book PDF (a single handout, a fan supplement page) without indexing.
---

# ttrpg-import-raw-pdf

## When to use

This is a **fallback** skill. The default path for any TTRPG book is
`ttrpg-import-book-pdf`, which calls marker internally and does post-processing.

Reach for `ttrpg-import-raw-pdf` only when:

- The user wants a quick text dump of a single PDF that is **not** going into
  the searchable library.
- An ingest failed mid-pipeline and you want to inspect raw marker output.
- The user is debugging marker's behavior on a specific page range.

## Direct invocation

Marker is a separate Python package the user installs themselves (see
`README.md` prereqs).

```bash
# Single file → markdown next to it
marker_single imports/books/handout.pdf imports/books/_marker_out

# With page range, preserving original images
marker_single imports/books/handout.pdf imports/books/_marker_out \
  --page_range 12-18 \
  --extract_images true

# Force CUDA if marker auto-detection is not enough
TORCH_DEVICE=cuda marker_single imports/books/handout.pdf \
  --output_dir imports/books/_marker_out \
  --output_format markdown \
  --layout_batch_size 8 \
  --detection_batch_size 8 \
  --recognition_batch_size 128

# Marker-maintainer-recommended pattern for image descriptions only:
# enable LLM, but override processors to just the image caption processor.
# book-ingest wraps this as: book-ingest --llm images-only <pdf>
marker_single imports/books/handout.pdf \
  --output_dir imports/books/_marker_out \
  --output_format markdown \
  --use_llm \
  --llm_service marker.services.openai.OpenAIService \
  --processors "marker.processors.llm.llm_image_description.LLMImageDescriptionProcessor"
```

Output lands in `imports/books/_marker_out/handout/`. **Do not commit this folder** —
it's intermediate. The proper destination for ingested content is
`vault/library/books/<slug>/`, which `book-ingest` handles correctly.

## GPU note

Marker's PyTorch install auto-selects CUDA when `torch.cuda.is_available()` is true.
For direct marker calls, force CUDA with `TORCH_DEVICE=cuda`. For normal book ingestion,
prefer `book-ingest --device cuda` so provenance captures the setting. The project shell routes Marker/qmd/HuggingFace/torch caches to project-local `.cache/`; preserve that cache across `.qmd/` wipeouts.

## Limitations to flag

Marker is good but not perfect on:

- Multi-column layouts with sidebars (occasional column reorder).
- Decorative drop caps (sometimes rendered as headings).
- Statblocks and heavily-formatted rules widgets (these are left as markdown;
  later conversion is judgment-based, not pre-parsed during ingest).

When the user complains about quality, the answer is usually "let book-ingest
handle the post-processing" rather than tweaking marker flags.
