"""The judged tier: one vision-model call, against a rubric you write.

Everything else in this library is deterministic and free. This is the part
that needs a key, because "the title overflows and collides with the logo" is
not measurable with ffprobe.

Requires the optional extra:  pip install "silentfail[vision]"
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ._core import SilentFail, existing, skip

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_SYSTEM = """You are a meticulous design reviewer checking a rendered image \
against a specific rubric.

Report only defects that are actually visible in the image. Do not speculate \
about what might be wrong, do not report subjective preferences, and do not \
invent findings to seem thorough -- an empty findings list is the correct \
answer for a clean image, and a false positive costs more than a miss because \
it trains the caller to ignore you.

Severity:
- critical: the image is unusable as shipped. Text is cut off or unreadable, \
content is missing, elements overlap so as to obscure each other.
- major: a viewer would notice something is wrong. Text overflows its \
container, elements collide, the layout is visibly broken or badly unbalanced.
- minor: a designer would fix it, a viewer would not notice. Slight spacing \
or alignment inconsistencies.

Judge each rubric item independently and report at most one finding per item."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "major", "minor"],
                    },
                    "rubric_item": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["severity", "rubric_item", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def looks_ok(
    image: str | Path,
    rubric: Iterable[str],
    *,
    model: str = "claude-opus-5",
    client: Any = None,
) -> list[dict[str, str]] | None:
    """Check a rendered image against `rubric`, a list of plain-English claims.

    The incident: slide titles that wrapped to three lines and collided with the
    logo, half-empty canvases, figures cropped mid-caption. All of them rendered
    without error, and all of them shipped until a human opened the deck.

    Raises on any critical or major finding; warns on minor ones. Returns the
    full findings list, or None if the check could not run.

        looks_ok("slide-14.png", ["the title fits on one line",
                                  "no text is clipped at any edge"])
    """
    path = existing(image)
    claims = [str(item).strip() for item in rubric if str(item).strip()]
    if not claims:
        raise ValueError("rubric is empty: there is nothing to check the image against")

    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(
            f"unsupported image type {path.suffix!r}; expected one of "
            f"{', '.join(sorted(_MEDIA_TYPES))}"
        )

    # Everything from here to the response is the fail-open boundary: a missing
    # key, a missing dependency, a rate limit, or a network blip means "could
    # not check", never "checked and it was fine".
    try:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        prompt = "Check this image against each of the following:\n" + "\n".join(
            f"{n}. {item}" for n, item in enumerate(claims, 1)
        )
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(path.read_bytes()).decode(),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        if response.stop_reason == "refusal":
            skip(f"looks_ok: the model declined to review {path}")
            return None
        body = "".join(b.text for b in response.content if b.type == "text")
        # Shape is guaranteed by the structured-output schema above, so this
        # annotation is a contract with mypy, not an unchecked assumption.
        findings: list[dict[str, str]] = json.loads(body)["findings"]
    except ImportError:
        skip('looks_ok: needs the vision extra -- pip install "silentfail[vision]"')
        return None
    except Exception as exc:
        detail = (
            "no ANTHROPIC_API_KEY set"
            if not os.environ.get("ANTHROPIC_API_KEY")
            else exc
        )
        skip(f"looks_ok: could not review {path} ({detail})")
        return None

    blocking = [f for f in findings if f["severity"] in ("critical", "major")]
    for finding in findings:
        if finding["severity"] == "minor":
            skip(f"looks_ok [minor] {path}: {finding['note']}")
    if blocking:
        worst = min(blocking, key=lambda f: f["severity"] != "critical")
        others = f" (+{len(blocking) - 1} more)" if len(blocking) > 1 else ""
        raise SilentFail(
            f"[{worst['severity']}] {path}: {worst['note']}{others} "
            f"-- failed rubric item: {worst['rubric_item']!r}"
        )
    return findings
