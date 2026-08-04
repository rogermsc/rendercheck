"""Project defaults, so a CI step is not eight flags on one line.

Thresholds are per-project, not per-invocation -- a shop rendering broadcast
deliverables wants the same numbers on every file, every time. Putting them in a
file also puts them in review, which a workflow line nobody reads does not.

    # rendercheck.toml
    preset = "ebu"
    max_silence = 5.0

Precedence, loosest to tightest: built-in defaults, this file, `--preset`, then
whatever flags were actually typed. A flag someone typed always wins.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

FILENAME = "rendercheck.toml"
SECTION = "pyproject.toml"

HAVE_TOML = sys.version_info >= (3, 11)
"""Whether this interpreter can read the config file.

`tomllib` arrived in 3.11. Rather than take a dependency to support one older
version -- against the whole point of this package having none -- 3.10 is told
plainly that the file is being ignored and to pass flags instead. Silently
ignoring it would be the failure this library is named after.
"""


def find(start: Path | None = None) -> Path | None:
    """The nearest config file, searching upward from `start`.

    Upward rather than alongside the media: renders land in an output directory
    that nobody wants to keep a config in, while the project root is where the
    thresholds belong.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        standalone = directory / FILENAME
        if standalone.is_file():
            return standalone
        project = directory / SECTION
        # A pyproject.toml with no section of ours is not our config file, and
        # stopping at one would hide a real config further up -- a package
        # directory inside a repo is an ordinary layout. Tested as text rather
        # than parsed because this decision has to work on 3.10 too, where there
        # is no TOML parser; the real read happens in `load`.
        if project.is_file():
            try:
                if "[tool.rendercheck]" in project.read_text(encoding="utf-8"):
                    return project
            except OSError:
                pass
    return None


def load(start: Path | None = None, known: set[str] | None = None) -> dict[str, Any]:
    """Read the nearest config file, or an empty dict if there is not one.

    `known` is the set of settings that exist. Anything else in the file is
    reported on stderr rather than ignored: a typo'd threshold that silently
    does nothing is the exact shape of bug this library exists to catch, and it
    would be absurd to ship one.
    """
    path = find(start)
    if path is None:
        return {}
    if not HAVE_TOML:  # pragma: no cover -- only reachable on 3.10
        print(
            f"rendercheck: ignoring {path} -- reading it needs Python 3.11 or "
            f"newer. Pass the thresholds as flags instead.",
            file=sys.stderr,
        )
        return {}

    import tomllib

    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"rendercheck: could not read {path}: {exc}", file=sys.stderr)
        return {}

    settings = (
        parsed.get("tool", {}).get("rendercheck", {})
        if path.name == SECTION
        else parsed
    )
    if not isinstance(settings, dict):
        return {}

    # TOML users write hyphens as readily as underscores; argparse only knows
    # underscores.
    settings = {str(key).replace("-", "_"): value for key, value in settings.items()}
    if known is not None:
        for key in sorted(set(settings) - known):
            print(
                f"rendercheck: {path} sets unknown option {key!r} -- ignoring it",
                file=sys.stderr,
            )
            settings.pop(key)
    return settings
