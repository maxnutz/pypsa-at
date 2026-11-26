# SPDX-FileCopyrightText: 2026 Infra-Ensure contributors
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Tests for the PyPSA-AT compatibility guard."""

import subprocess

import pytest

from infra_ensure import compat

START = f"# {compat.START}"
END = f"# {compat.END}"

REGISTRY = {
    "owned_paths": ["infra_ensure/**", "rules/infra-ensure/**"],
    "touchpoints": {
        "Snakefile": {"path": "Snakefile"},
        "data.csv": {"path": "data.csv", "markers": False},
    },
}


def run_check(changed, hunks, files):
    return compat.check(changed, hunks, REGISTRY, read=lambda p: files[p])


def test_registry_loads_and_touchpoints_exist():
    registry = compat.load_registry()
    assert registry["owned_paths"]
    for path, touchpoint in registry["touchpoints"].items():
        assert touchpoint["reason"], path
        assert touchpoint["upstream"] in {"none", "pypsa-at", "pypsa-de", "pypsa-eur"}


def test_registered_touchpoints_are_not_owned():
    registry = compat.load_registry()
    for path in registry["touchpoints"]:
        assert not compat.is_owned(path, registry["owned_paths"]), path


@pytest.mark.parametrize(
    ("path", "owned"),
    [
        ("infra_ensure/compat.py", True),
        ("rules/infra-ensure/collect.smk", True),
        ("rules/pypsa-at/collect.smk", False),
    ],
)
def test_is_owned(path, owned):
    assert compat.is_owned(path, REGISTRY["owned_paths"]) is owned


def test_parse_hunks():
    diff = "\n".join(
        [
            "+++ b/Snakefile",
            "@@ -10,0 +11,3 @@",
            "@@ -20 +23 @@",
            "@@ -30,2 +32,0 @@",
            "+++ /dev/null",
            "@@ -1,5 +0,0 @@",
        ]
    )
    assert compat.parse_hunks(diff) == {"Snakefile": [(11, 3), (23, 1), (32, 0)]}


def test_marker_blocks():
    lines = ["a", START, "b", END, "c", START, "d", END]
    assert compat.marker_blocks(lines) == ([(2, 4), (6, 8)], [])


@pytest.mark.parametrize(
    "lines", [[START, "a"], ["a", END], [START, START, END]], ids=str
)
def test_marker_blocks_unbalanced(lines):
    assert compat.marker_blocks(lines)[1]


def test_unmarked_lines():
    blocks = [(2, 5)]
    assert compat.unmarked_lines([(3, 2)], blocks) == []
    assert compat.unmarked_lines([(4, 3)], blocks) == [6]
    assert compat.unmarked_lines([(1, 0)], blocks) == [1]
    assert compat.unmarked_lines([(4, 3)], blocks, blank={6}) == []


def test_check_passes_for_owned_and_marked_changes():
    files = {"Snakefile": ["a", START, "new", END, "b"]}
    changed = {"infra_ensure/x.py": "A", "Snakefile": "M"}
    errors, warnings = run_check(changed, {"Snakefile": [(2, 3)]}, files)
    assert errors == []
    assert [w.path for w in warnings] == ["data.csv"]


def test_check_fails_for_unregistered_file():
    errors, _ = run_check({"rules/pypsa-at/collect.smk": "M"}, {}, {})
    assert [e.path for e in errors] == ["rules/pypsa-at/collect.smk"]


def test_check_fails_for_change_outside_markers():
    files = {"Snakefile": ["changed", START, "new", END]}
    errors, _ = run_check({"Snakefile": "M"}, {"Snakefile": [(1, 1)]}, files)
    assert "outside marker blocks: 1" in errors[0].message


def test_check_skips_markers_when_disabled():
    errors, _ = run_check({"data.csv": "M"}, {"data.csv": [(1, 1)]}, {})
    assert errors == []


def test_check_fails_for_deleted_touchpoint():
    errors, _ = run_check({"Snakefile": "D"}, {}, {})
    assert "deleted" in errors[0].message


def test_changed_files_against_git_repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "Snakefile").write_text("a\nb\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("branch", "base")
    (tmp_path / "Snakefile").write_text(f"a\n{START}\nnew\n{END}\nb\n")

    monkeypatch.chdir(tmp_path)
    changed, hunks = compat.changed_files("base")
    assert changed == {"Snakefile": "M"}
    assert hunks == {"Snakefile": [(2, 3)]}
