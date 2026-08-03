"""Invariants the release workflow depends on.

Every one of these is checked again inside `.github/workflows/release.yml`,
but a release is the worst place to find out: the tag already exists, the
draft is already published, and the fix means cutting another one. These fail
in the pull request instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "njtransit"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text())
HACS = json.loads((ROOT / "hacs.json").read_text())

# Substituted by the release workflow. Deliberately not a plausible version:
# a placeholder of "0.0.0" would silently no-op if someone set a real one.
VERSION_PLACEHOLDER = "0000.0.0"


def test_manifest_keeps_the_version_placeholder() -> None:
    """A hardcoded version would fail the release, not the build.

    The workflow refuses to run if the placeholder is gone, precisely so a
    stale hand-edited version never ships. This surfaces that earlier.
    """
    assert MANIFEST["version"] == VERSION_PLACEHOLDER


def test_hacs_filename_matches_what_the_workflow_builds() -> None:
    """hacs.json names the asset HACS downloads.

    If these drift, every install fails at download time with nothing wrong
    in the logs here.
    """
    assert HACS["filename"] == "njtransit.zip"
    assert HACS["zip_release"] is True

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "njtransit.zip" in workflow


def test_domain_matches_the_directory() -> None:
    """HACS extracts by domain; a mismatch installs into the wrong folder."""
    assert MANIFEST["domain"] == INTEGRATION.name


@pytest.mark.parametrize(
    "required",
    ["manifest.json", "__init__.py", "strings.json", "translations/en.json"],
)
def test_shipped_files_exist(required: str) -> None:
    """Files the release archive asserts are present."""
    assert (INTEGRATION / required).is_file()


def test_manifest_keys_are_sorted_for_hassfest() -> None:
    """domain and name first, then alphabetical.

    hassfest enforces this and it is easy to break when adding a key; the
    baseline manifest satisfied it by accident until config_flow was added.
    """
    keys = list(MANIFEST)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_no_external_requirements() -> None:
    """The API client is bundled, so nothing is installed at setup.

    If this ever grows an entry, the release notes and README need to say so
    -- and it becomes a reason to extract `api/` to PyPI properly.
    """
    assert MANIFEST["requirements"] == []
