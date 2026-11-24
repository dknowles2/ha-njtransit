"""Invariants the release workflow depends on.

Every one of these is checked again inside `.github/workflows/release.yml`,
but a release is the worst place to find out: the tag already exists, the
draft is already published, and the fix means cutting another one. These fail
in the pull request instead.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
INTEGRATION = ROOT / "custom_components" / "njtransit"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text())
HACS = json.loads((ROOT / "hacs.json").read_text())

SHA_PIN_RE = re.compile(r"@([0-9a-f]{40})$")

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


def _stamp_step() -> dict[str, Any]:
    workflow = yaml.safe_load((WORKFLOWS_DIR / "release.yml").read_text())
    steps: list[dict[str, Any]] = workflow["jobs"]["release"]["steps"]
    for step in steps:
        if step.get("name") == "Stamp the release version into the manifest":
            return step
    raise AssertionError("Stamp step not found in release.yml")


def test_release_stamp_step_passes_tag_via_env() -> None:
    """The stamp step must pass TAG through env rather than interpolating into shell."""
    step = _stamp_step()
    assert "TAG" in step.get("env", {})
    assert "${{" not in step["run"]


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("2026.8.0", "2026.8.0"),
        ("v2026.8.0", "2026.8.0"),
        ("2026.8.0-rc1", "2026.8.0-rc1"),
        ("v2026.8.0.dev1", "2026.8.0.dev1"),
    ],
)
def test_release_stamp_step_updates_manifest_for_valid_tags(
    tmp_path: Path, tag: str, expected: str
) -> None:
    """Valid CalVer tags stamp manifest.json cleanly and preserve key order."""
    manifest_dir = tmp_path / "custom_components" / "njtransit"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text((INTEGRATION / "manifest.json").read_text())

    step = _stamp_step()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "TAG": tag},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    stamped = json.loads(manifest_path.read_text())
    assert stamped["version"] == expected
    keys = list(stamped)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


@pytest.mark.parametrize(
    "bad_tag",
    [
        "",
        "$(id)",
        '2026.8.0"; id #',
        "2026.8.0' + 'x",
        "2026.8.0/../../etc/passwd",
        "2026.8.0&touch pwned",
        "2026.8.0\n",
        "not-a-version",
    ],
)
def test_release_stamp_step_rejects_malicious_tags(
    tmp_path: Path, bad_tag: str
) -> None:
    """Crafted tags containing shell metacharacters or invalid syntax fail safely."""
    manifest_dir = tmp_path / "custom_components" / "njtransit"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "manifest.json"
    original = (INTEGRATION / "manifest.json").read_text()
    manifest_path.write_text(original)

    step = _stamp_step()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "TAG": bad_tag},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert manifest_path.read_text() == original
    assert not (tmp_path / "pwned").exists()


def test_release_stamp_step_rejects_missing_placeholder(tmp_path: Path) -> None:
    """Stamping fails loudly if manifest.json no longer has 0000.0.0."""
    manifest_dir = tmp_path / "custom_components" / "njtransit"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "manifest.json"
    bad_manifest = {**MANIFEST, "version": "2026.7.0"}
    manifest_path.write_text(json.dumps(bad_manifest, indent=2) + "\n")

    step = _stamp_step()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "TAG": "2026.8.0"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert json.loads(manifest_path.read_text())["version"] == "2026.7.0"


def test_workflows_have_permissions_and_sha_pinned_actions() -> None:
    """Every workflow sets top-level permissions and pins actions to 40-char SHAs."""
    for workflow_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        data = yaml.safe_load(workflow_path.read_text())
        assert "permissions" in data, (
            f"{workflow_path.name} missing top-level permissions"
        )
        for job_name, job in data.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses is not None:
                    assert SHA_PIN_RE.search(uses), (
                        f"{workflow_path.name}:{job_name} action {uses!r} is not SHA-pinned"
                    )


def test_ci_workflow_invariants() -> None:
    """release-drafter re-labels edited PRs and CI enforces deprecation errors."""
    drafter = yaml.safe_load((WORKFLOWS_DIR / "release-drafter.yml").read_text())
    assert "edited" in drafter["on"]["pull_request"]["types"]

    build_test = yaml.safe_load((WORKFLOWS_DIR / "build-and-test.yml").read_text())
    pytest_steps = [
        step["run"]
        for step in build_test["jobs"]["test"]["steps"]
        if "pytest" in step.get("run", "")
    ]
    assert any("-W error::DeprecationWarning" in run for run in pytest_steps)


def test_the_release_draft_is_numbered_by_calver() -> None:
    """The draft's version comes from `next_calver.py`, not the resolver.

    Release-drafter alone bumps a position on the previous tag and so proposed
    `2026.9.4` in November. Dropping the `version` input, or the step feeding
    it, puts that back -- with nothing failing, since a wrong number is still
    a number.
    """
    drafter = yaml.safe_load((WORKFLOWS_DIR / "release-drafter.yml").read_text())
    steps = drafter["jobs"]["update_release_draft"]["steps"]
    calver = [step for step in steps if step.get("id") == "calver"]
    assert calver, "no step computes the CalVer version"
    assert "scripts/next_calver.py" in calver[0]["run"]
    assert "|| true" not in calver[0]["run"], "a failed lookup must fail the job"

    action = [
        step
        for step in steps
        if "release-drafter/release-drafter@" in step.get("uses", "")
    ]
    assert action[0]["with"]["version"] == "${{ steps.calver.outputs.version }}"
    assert steps.index(calver[0]) < steps.index(action[0])


def test_the_release_draft_rolls_over_on_the_first() -> None:
    """A draft last written in October must not still read October in November."""
    drafter = yaml.safe_load((WORKFLOWS_DIR / "release-drafter.yml").read_text())
    crons = [entry["cron"] for entry in drafter["on"]["schedule"]]
    assert any(cron.split()[2] == "1" for cron in crons)
    # The job must run on that schedule, not only on pushes -- it was gated
    # on `push` alone before, which would have made the cron a no-op.
    assert (
        drafter["jobs"]["update_release_draft"]["if"]
        == "github.event_name != 'pull_request'"
    )
