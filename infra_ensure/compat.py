# SPDX-FileCopyrightText: 2026 Infra-Ensure contributors
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
PyPSA-AT compatibility guard.

Compares the working tree with the merge base of ``upstream/main`` (AGGM-AG/pypsa-at)
and checks that every change outside the Infra-Ensure paths is registered in
``infra_ensure/touchpoints.yaml`` and, for marked touch points, lies inside an
Infra-Ensure marker block.

Usage::

    pixi run check-at-compat               # against upstream/main
    pixi run check-at-compat --base <ref>  # against another ref
"""

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REGISTRY = Path(__file__).parent / "touchpoints.yaml"
START = "--- START: Block Maintained by Infra-Ensure ---"
END = "--- END: Block Maintained by Infra-Ensure ---"
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Violation:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def load_registry(path: Path = REGISTRY) -> dict:
    """Load the registry and index the touch points by path."""
    registry = yaml.safe_load(path.read_text())
    registry["touchpoints"] = {tp["path"]: tp for tp in registry["touchpoints"]}
    return registry


def is_owned(path: str, owned_paths: list[str]) -> bool:
    """Return True if the path matches one of the owned glob patterns."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in owned_paths)


def parse_hunks(diff: str) -> dict[str, list[tuple[int, int]]]:
    """
    Parse a ``git diff -U0`` into the changed line ranges of the new files.

    Returns ``{path: [(first_line, n_lines), ...]}``. A pure deletion has
    ``n_lines == 0`` and ``first_line`` is the line before the deletion.
    """
    hunks: dict[str, list[tuple[int, int]]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = None if line == "+++ /dev/null" else line[len("+++ b/") :]
            if path is not None:
                hunks.setdefault(path, [])
        elif (match := HUNK.match(line)) and path is not None:
            count = 1 if match.group(2) is None else int(match.group(2))
            hunks[path].append((int(match.group(1)), count))
    return hunks


def marker_blocks(lines: list[str]) -> tuple[list[tuple[int, int]], list[str]]:
    """
    Find the Infra-Ensure marker blocks (1-based, inclusive line ranges).

    Returns the blocks and a list of errors for unbalanced or nested markers.
    """
    blocks, errors = [], []
    start = None
    for number, line in enumerate(lines, start=1):
        if START in line:
            if start is not None:
                errors.append(f"line {number}: START inside an open block")
            start = number
        elif END in line:
            if start is None:
                errors.append(f"line {number}: END without START")
            else:
                blocks.append((start, number))
                start = None
    if start is not None:
        errors.append(f"line {start}: START without END")
    return blocks, errors


def unmarked_lines(
    hunks: list[tuple[int, int]],
    blocks: list[tuple[int, int]],
    blank: set[int] = frozenset(),
) -> list[int]:
    """
    Return the changed lines that are not inside a marker block.

    Added lines listed in ``blank`` are skipped: formatters (snakefmt, ruff)
    need blank lines around marker blocks.
    """

    def inside(line: int) -> bool:
        return any(first <= line <= last for first, last in blocks)

    outside = []
    for first, count in hunks:
        if count == 0:
            lines = [first]
        else:
            lines = [n for n in range(first, first + count) if n not in blank]
        outside += [line for line in lines if not inside(line)]
    return outside


def check(
    changed: dict[str, str],
    hunks: dict[str, list[tuple[int, int]]],
    registry: dict,
    read=lambda path: Path(path).read_text().splitlines(),
) -> tuple[list[Violation], list[Violation]]:
    """
    Check the changed files against the registry.

    Parameters
    ----------
    changed
        ``{path: status}`` with git status letters (A, M, D, ...).
    hunks
        Changed line ranges from :func:`parse_hunks`.
    registry
        Registry from :func:`load_registry`.
    read
        Returns the lines of a file in the working tree.

    Returns
    -------
    Errors and warnings.
    """
    errors, warnings = [], []
    touchpoints = registry["touchpoints"]
    for path, status in sorted(changed.items()):
        if is_owned(path, registry["owned_paths"]):
            continue
        if path not in touchpoints:
            errors.append(
                Violation(path, "changed but not registered in touchpoints.yaml")
            )
            continue
        if status == "D":
            errors.append(Violation(path, "PyPSA-AT file deleted"))
            continue
        if not touchpoints[path].get("markers", True):
            continue
        lines = read(path)
        blocks, marker_errors = marker_blocks(lines)
        errors += [Violation(path, message) for message in marker_errors]
        blank = {n for n, line in enumerate(lines, start=1) if not line.strip()}
        outside = unmarked_lines(hunks.get(path, []), blocks, blank)
        if outside:
            numbers = ", ".join(map(str, outside[:10]))
            more = " ..." if len(outside) > 10 else ""
            errors.append(
                Violation(path, f"changed lines outside marker blocks: {numbers}{more}")
            )
    for path in sorted(set(touchpoints) - set(changed)):
        warnings.append(Violation(path, "registered but unchanged (stale entry?)"))
    return errors, warnings


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def changed_files(base: str) -> tuple[dict[str, str], dict[str, list]]:
    """Return changed files and hunks of the working tree against the merge base."""
    merge_base = git("merge-base", base, "HEAD").strip()
    status = git("diff", "--name-status", "--no-renames", merge_base)
    changed = {}
    for line in status.splitlines():
        letter, path = line.split("\t", 1)
        changed[path] = letter[0]
    # Hunks that only add or delete blank lines are dropped, and so are
    # whitespace changes at line ends (e.g. a newline added at the end of a
    # file before appending a marker block).
    diff = git(
        "diff",
        "-U0",
        "--no-renames",
        "--no-color",
        "--ignore-blank-lines",
        "--ignore-space-at-eol",
        merge_base,
    )
    return changed, parse_hunks(diff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="upstream/main", help="PyPSA-AT ref")
    args = parser.parse_args(argv)

    changed, hunks = changed_files(args.base)
    errors, warnings = check(changed, hunks, load_registry())
    for warning in warnings:
        print(f"WARNING {warning}")
    for error in errors:
        print(f"ERROR   {error}")
    touched = [p for p in changed if not is_owned(p, load_registry()["owned_paths"])]
    print(
        f"{len(changed)} files differ from {args.base}, "
        f"{len(touched)} of them PyPSA-AT files: "
        f"{len(errors)} errors, {len(warnings)} warnings"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
