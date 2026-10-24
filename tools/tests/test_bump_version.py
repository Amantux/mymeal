"""Tests for the release bump, which used to be an untested heredoc in CI.

Its parsing rules are not obvious and one of them has already caused an
incident: a commit whose BODY mentioned "[minor]" bumped the minor version and
turned a release into 1.0.0. That rule now has a test.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bump_version as bv  # noqa: E402


@pytest.mark.parametrize("subject,expected", [
    ("fix(units): something", "1.2.4"),
    ("chore: tidy", "1.2.4"),
    ("feat: add thing [minor]", "1.3.0"),
    ("feat!: rewrite [major]", "2.0.0"),
])
def test_the_subject_decides_the_bump(subject, expected):
    assert bv.next_version("1.2.3", subject) == expected


def test_only_the_SUBJECT_is_scanned_not_the_body():
    """REGRESSION. A commit body that merely MENTIONS [minor] — documentation
    describing the bump rules, say — must not bump the minor version. This
    happened once and turned a release into 1.0.0."""
    message = "docs: explain the release rules\n\nThe subject may contain [minor] or [major].\n"

    assert bv.next_version("1.2.3", message) == "1.2.4"


def test_an_empty_or_missing_message_still_patches():
    assert bv.next_version("0.9.9", "") == "0.9.10"
    assert bv.next_version("0.9.9", None) == "0.9.10"


def test_a_user_facing_commit_gets_a_real_changelog_note():
    """HA renders this in the Changelog tab. "Build-only republish" on a commit
    that changed something tells a user deciding whether to update that there
    is nothing to see."""
    entry = bv.changelog_entry("1.3.0", "feat(mealplan): copy a week to another week")

    assert "## 1.3.0" in entry
    assert "- Copy a week to another week." in entry
    assert "Build-only" not in entry


def test_a_chore_gets_the_stub():
    entry = bv.changelog_entry("1.2.4", "chore: bump a dependency")

    assert "Build-only republish" in entry


def test_apply_moves_all_three_files_in_lockstep(tmp_path, monkeypatch):
    """config.yaml and manifest.json versions are asserted equal by CI, so a
    bump that moved one and not the other would break the build."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mymeal").mkdir()
    (tmp_path / "custom_components" / "mymeal").mkdir(parents=True)
    (tmp_path / bv.CONFIG).write_text('name: myMeal\nversion: "1.2.3"\nslug: mymeal\n')
    (tmp_path / bv.MANIFEST).write_text(json.dumps({"domain": "mymeal", "version": "1.2.3"}))
    (tmp_path / bv.CHANGELOG).write_text("# Changelog\n\n## 1.2.3\n\n- Previous.\n")

    old, new = bv.apply("feat: a new thing")

    assert (old, new) == ("1.2.3", "1.2.4")
    assert 'version: "1.2.4"' in (tmp_path / bv.CONFIG).read_text()
    assert json.loads((tmp_path / bv.MANIFEST).read_text())["version"] == "1.2.4"
    assert "## 1.2.4" in (tmp_path / bv.CHANGELOG).read_text()


def test_an_existing_heading_for_the_new_version_is_not_duplicated(
        tmp_path, monkeypatch):
    """MUTATION GUARD. Someone can hand-write a changelog section for the
    version that is about to be minted — drafting release notes ahead of the
    bump is normal. The bump must then leave it alone rather than inserting a
    second "## 1.2.4" above it, which renders as two sections for one release
    in Home Assistant's Changelog tab.

    (My first attempt at this test called apply() twice and asserted no
    duplicate. That passed with the guard REMOVED, because the second call
    computes a different version and never collides — a test that existed
    without catching anything. The mutation run is what exposed it.)"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mymeal").mkdir()
    (tmp_path / "custom_components" / "mymeal").mkdir(parents=True)
    (tmp_path / bv.CONFIG).write_text('version: "2.0.0"\n')
    (tmp_path / bv.MANIFEST).write_text(json.dumps({"version": "2.0.0"}))
    # 2.0.1 is what the bump will mint, and it is ALREADY in the changelog.
    (tmp_path / bv.CHANGELOG).write_text(
        "# Changelog\n\n## 2.0.1\n\n- Drafted ahead of the release.\n"
        "\n## 2.0.0\n\n- Previous.\n")

    old, new = bv.apply("fix: one")

    text = (tmp_path / bv.CHANGELOG).read_text()
    assert (old, new) == ("2.0.0", "2.0.1")
    assert text.count("## 2.0.1") == 1, "the bump duplicated an existing section"
    assert "Drafted ahead of the release." in text, "it overwrote the hand-written note"
