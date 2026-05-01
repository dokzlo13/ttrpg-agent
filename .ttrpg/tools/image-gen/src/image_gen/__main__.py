from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import textwrap
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
from openai import OpenAI

DEFAULT_MODEL = "gpt-image-1"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "auto"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_DEST = "vault/notes/images"


@dataclass(frozen=True)
class RequestConfig:
    prompt: str
    title: str
    slug: str
    provider: str
    model: str
    size: str
    quality: str
    output_format: str
    dest: str


@dataclass(frozen=True)
class PlannedAsset:
    image_path: Path
    note_path: Path
    markdown_embed: str
    created: str
    request: RequestConfig


def parse_dotenv(text: str) -> dict[str, str]:
    """Tiny dotenv parser for KEY=value lines. Does not expand variables."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        out[key] = value
    return out


def load_dotenv_into(env: dict[str, str], dotenv_path: Path) -> None:
    if not dotenv_path.is_file():
        return
    parsed = parse_dotenv(dotenv_path.read_text(encoding="utf-8", errors="replace"))
    for key, value in parsed.items():
        env.setdefault(key, value)


def find_project_root() -> Path:
    env_root = os.environ.get("TTRPG_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".ttrpg" / "tools" / "image-gen").exists():
            return candidate
        if (candidate / ".git").exists() and (candidate / ".env.example").exists():
            return candidate
    return cwd


def slugify(value: str, *, max_len: int = 56) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    value = re.sub(r"-+", "-", value)
    if not value:
        value = "generated-image"
    return value[:max_len].strip("-") or "generated-image"


def title_from_prompt(prompt: str) -> str:
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), "Generated Image")
    first_line = re.sub(r"^(draw|create|generate|illustrate)\s+(an?|the)?\s*", "", first_line, flags=re.I)
    first_line = first_line.strip(" .,:;!?\"'")
    if not first_line:
        return "Generated Image"
    words = first_line.split()[:8]
    title = " ".join(words).title()
    return title.strip(" .,:;!?\"'") or "Generated Image"


def ensure_under_images_dir(dest: Path, project_root: Path) -> Path:
    resolved = dest.expanduser()
    if not resolved.is_absolute():
        resolved = project_root / resolved
    resolved = resolved.resolve()

    images_root = (project_root / "vault" / "notes" / "images").resolve()
    try:
        resolved.relative_to(images_root)
    except ValueError as exc:
        raise click.ClickException(
            "destination must be under vault/notes/images for Obsidian/qmd asset adoption; "
            f"got {resolved}"
        ) from exc
    return resolved


def make_request_config(
    *,
    prompt: str,
    title: str | None,
    slug: str | None,
    model: str,
    size: str,
    quality: str,
    output_format: str,
    dest: str,
) -> RequestConfig:
    resolved_title = title or title_from_prompt(prompt)
    resolved_slug = slugify(slug or resolved_title or prompt)
    return RequestConfig(
        prompt=prompt,
        title=resolved_title,
        slug=resolved_slug,
        provider="openai",
        model=model,
        size=size,
        quality=quality,
        output_format=output_format,
        dest=dest,
    )


def plan_asset(config: RequestConfig, dest: Path) -> PlannedAsset:
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    digest_source = json.dumps(asdict(config), sort_keys=True) + created + uuid4().hex
    short_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:8]
    base_name = f"{config.slug}-{short_hash}"
    image_path = dest / f"{base_name}.{config.output_format}"
    note_path = dest / f"{base_name}.md"
    return PlannedAsset(
        image_path=image_path,
        note_path=note_path,
        markdown_embed=f"![{config.title}]({image_path.name})",
        created=created,
        request=config,
    )


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return jsonable(value.dict())
    return str(value)


def write_asset_note(
    *,
    planned: PlannedAsset,
    response_metadata: dict[str, Any],
    prompt_source: str,
) -> None:
    request = planned.request
    frontmatter = {
        "type": "handout",
        "source": "agent",
        "created": planned.created[:10],
        "tags": ["campaign", "image-generation", "asset"],
        "status": "draft",
        "asset_kind": "image",
        "image_file": planned.image_path.name,
        "provider": request.provider,
        "model": request.model,
        "size": request.size,
        "quality": request.quality,
        "output_format": request.output_format,
    }

    frontmatter_lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            frontmatter_lines.append(f"{key}: [{', '.join(yaml_scalar(v) for v in value)}]")
        else:
            frontmatter_lines.append(f"{key}: {yaml_scalar(value)}")
    frontmatter_lines.append("---")

    params = {
        "provider": request.provider,
        "prompt_source": prompt_source,
        "api_request": {
            "model": request.model,
            "prompt": request.prompt,
            "size": request.size,
            "quality": request.quality,
            "output_format": request.output_format,
            "n": 1,
        },
        "output": {
            "image_file": planned.image_path.name,
            "note_file": planned.note_path.name,
            "markdown_embed": planned.markdown_embed,
        },
    }
    params_json = json.dumps(params, indent=2, ensure_ascii=False)
    response_json = json.dumps(jsonable(response_metadata), indent=2, ensure_ascii=False, sort_keys=True)

    body = f"""
# {request.title}

{planned.markdown_embed}

## Prompt

```text
{request.prompt.rstrip()}
```

## Generation Parameters

```json
{params_json}
```

## Generation Result

```json
{response_json}
```

## Adoption Notes

- Generated as a reusable visual asset.
- Not yet attached to a campaign entity. When adopted, link this note from the NPC, location, scene, handout, or session note and add a relevant wikilink under Connections.

## Connections

- Add links here when this asset is adopted into the active campaign graph.

## Sources

- Generated with OpenAI Images API via `.ttrpg/tools/image-gen`.
""".lstrip()

    planned.note_path.write_text("\n".join(frontmatter_lines) + "\n" + body, encoding="utf-8")


def save_image_from_response(response: Any, output_path: Path) -> dict[str, Any]:
    if not getattr(response, "data", None):
        raise click.ClickException("OpenAI returned no image data")

    image = response.data[0]
    b64_json = getattr(image, "b64_json", None)
    url = getattr(image, "url", None)

    if b64_json:
        image_bytes = base64.b64decode(b64_json)
    elif url:
        with urllib.request.urlopen(url, timeout=120) as handle:  # noqa: S310 - OpenAI-provided URL fallback.
            image_bytes = handle.read()
    else:
        raise click.ClickException("OpenAI response contained neither b64_json nor url")

    output_path.write_bytes(image_bytes)

    metadata: dict[str, Any] = {
        "created": getattr(response, "created", None),
        "revised_prompt": getattr(image, "revised_prompt", None),
        "usage": jsonable(getattr(response, "usage", None)),
    }
    return {key: value for key, value in metadata.items() if value is not None}


def emit_text(planned: PlannedAsset, *, dry_run: bool) -> None:
    prefix = "dry-run: " if dry_run else ""
    click.echo(f"{prefix}image: {planned.image_path}")
    click.echo(f"{prefix}note:  {planned.note_path}")
    click.echo(f"{prefix}embed: {planned.markdown_embed}")


def emit_json(planned: PlannedAsset, *, dry_run: bool, response_metadata: dict[str, Any] | None) -> None:
    payload = {
        "dry_run": dry_run,
        "image_path": str(planned.image_path),
        "note_path": str(planned.note_path),
        "markdown_embed": planned.markdown_embed,
        "created": planned.created,
        "request": asdict(planned.request),
        "response": response_metadata or {},
    }
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--subject",
    "prompt_text",
    help="Prompt/subject to send to the image model. Prefer a complete image prompt.",
)
@click.option(
    "--prompt",
    "prompt_alias",
    help="Alias for --subject; useful when passing a full prompt.",
)
@click.option(
    "--prompt-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the prompt from a UTF-8 text/markdown file.",
)
@click.option("--title", help="Human-readable title for the adjacent Markdown asset note.")
@click.option("--slug", help="Filename slug base. Defaults to a slug derived from the title/prompt.")
@click.option("--dest", default=None, help=f"Output directory under vault/notes/images. Default: {DEFAULT_DEST}.")
@click.option("--model", default=None, help=f"OpenAI image model. Default: env TTRPG_IMAGE_MODEL or {DEFAULT_MODEL}.")
@click.option("--size", default=None, help=f"Image size. Default: env TTRPG_IMAGE_SIZE or {DEFAULT_SIZE}.")
@click.option("--quality", default=None, help=f"Image quality. Default: env TTRPG_IMAGE_QUALITY or {DEFAULT_QUALITY}.")
@click.option(
    "--output-format",
    type=click.Choice(["png", "jpeg", "webp"]),
    default=None,
    help=f"Output format. Default: env TTRPG_IMAGE_OUTPUT_FORMAT or {DEFAULT_OUTPUT_FORMAT}.",
)
@click.option("--dry-run", is_flag=True, help="Plan paths and metadata without calling OpenAI or writing files.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def main(
    prompt_text: str | None,
    prompt_alias: str | None,
    prompt_file: Path | None,
    title: str | None,
    slug: str | None,
    dest: str | None,
    model: str | None,
    size: str | None,
    quality: str | None,
    output_format: str | None,
    dry_run: bool,
    json_output: bool,
) -> None:
    """Generate one image and an adjacent qmd-indexable Markdown asset note."""
    project_root = find_project_root()
    env = dict(os.environ)
    load_dotenv_into(env, project_root / ".env")

    prompt_sources = [value for value in [prompt_text, prompt_alias] if value]
    if prompt_file:
        prompt_sources.append(prompt_file.read_text(encoding="utf-8", errors="replace"))
    if len(prompt_sources) != 1:
        raise click.ClickException("provide exactly one of --subject, --prompt, or --prompt-file")
    prompt = textwrap.dedent(prompt_sources[0]).strip()
    if not prompt:
        raise click.ClickException("prompt must not be empty")

    resolved_dest_text = dest or env.get("TTRPG_IMAGE_OUTPUT_DIR") or DEFAULT_DEST
    resolved_dest = ensure_under_images_dir(Path(resolved_dest_text), project_root)
    config = make_request_config(
        prompt=prompt,
        title=title,
        slug=slug,
        model=model or env.get("TTRPG_IMAGE_MODEL") or DEFAULT_MODEL,
        size=size or env.get("TTRPG_IMAGE_SIZE") or DEFAULT_SIZE,
        quality=quality or env.get("TTRPG_IMAGE_QUALITY") or DEFAULT_QUALITY,
        output_format=output_format or env.get("TTRPG_IMAGE_OUTPUT_FORMAT") or DEFAULT_OUTPUT_FORMAT,
        dest=str(resolved_dest.relative_to(project_root)),
    )
    if config.output_format not in {"png", "jpeg", "webp"}:
        raise click.ClickException("output format must be one of: png, jpeg, webp")

    planned = plan_asset(config, resolved_dest)

    if dry_run:
        if json_output:
            emit_json(planned, dry_run=True, response_metadata=None)
        else:
            emit_text(planned, dry_run=True)
        return

    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        raise click.ClickException("OPENAI_API_KEY is not set. Add it to .env or export it.")

    resolved_dest.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=api_key)
    try:
        response = client.images.generate(
            model=config.model,
            prompt=config.prompt,
            size=config.size,
            quality=config.quality,
            output_format=config.output_format,
            n=1,
        )
    except Exception as exc:  # noqa: BLE001 - surface SDK errors as CLI errors.
        raise click.ClickException(f"OpenAI image generation failed: {exc}") from exc

    response_metadata = save_image_from_response(response, planned.image_path)
    write_asset_note(planned=planned, response_metadata=response_metadata, prompt_source="cli")

    if json_output:
        emit_json(planned, dry_run=False, response_metadata=response_metadata)
    else:
        emit_text(planned, dry_run=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        click.echo("interrupted", err=True)
        sys.exit(130)
