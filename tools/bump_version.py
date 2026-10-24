#!/usr/bin/env python3
"""Bump the add-on version, the integration manifest and the changelog.

Lifted verbatim out of the `bump-version` job in .github/workflows/ci.yml so it
can be RE-RUN after a failed push. The job used to compute the bump once and
push once; when main moved in between — another session, another agent, a
merged PR — the push was rejected non-fast-forward and the whole release job
went red. Two of six consecutive runs died that way. Retrying has to recompute
from the newly-fetched files, because by then the version it was going to write
may already exist, so a dumb retry would try to mint a duplicate.

Being a file rather than a heredoc also means it is importable, so the parsing
rules below finally have tests.

The three files move in LOCKSTEP: config.yaml and manifest.json versions are
asserted equal by CI, and Home Assistant renders CHANGELOG.md in the add-on's
Changelog tab.
"""
from __future__ import annotations

import json
import os
import re
import sys

CONFIG = "mymeal/config.yaml"
MANIFEST = "custom_components/mymeal/manifest.json"
CHANGELOG = "mymeal/CHANGELOG.md"

_VERSION_RE = re.compile(r'^version:\s*"?([0-9]+\.[0-9]+\.[0-9]+)"?', re.M)
_CONVENTIONAL = re.compile(r"^(feat|fix)(\([^)]*\))?!?:\s*(.+)$", re.I)


def current_version(config_text: str) -> str:
    m = _VERSION_RE.search(config_text)
    if not m:
        raise SystemExit(f"no version found in {CONFIG}")
    return m.group(1)


def next_version(old: str, commit_subject: str) -> str:
    """The version after `old`, given the triggering commit's SUBJECT.

    Only the subject is scanned, never the body: a commit whose BODY merely
    mentions "[minor]" — a docs change describing this very feature, say —
    would otherwise bump. That happened once and turned a [minor] into 1.0.0.
    """
    major, minor, patch = (int(x) for x in old.split("."))
    subject = (commit_subject or "").splitlines()[0].lower() if commit_subject else ""
    if "[major]" in subject:
        return f"{major + 1}.0.0"
    if "[minor]" in subject:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def changelog_entry(new: str, commit_subject: str) -> str:
    """A real note for user-facing work, a stub for everything else.

    HA shows this in the Changelog tab, so "build-only republish" on a commit
    that actually changed something is worse than useless — it tells a user
    deciding whether to update that there is nothing to see.
    """
    subject = (commit_subject or "").splitlines()[0].strip() if commit_subject else ""
    m = _CONVENTIONAL.match(subject)
    if not m:
        return f"\n## {new}\n\nBuild-only republish; no user-visible change.\n"
    note = m.group(3).strip().rstrip(".")
    return f"\n## {new}\n\n- {note[:1].upper() + note[1:]}.\n"


def apply(commit_subject: str) -> tuple[str, str]:
    """Write all three files. Returns (old, new). Idempotent per working tree."""
    text = open(CONFIG).read()
    old = current_version(text)
    new = next_version(old, commit_subject)

    open(CONFIG, "w").write(
        re.sub(r"^(version:\s*).*$", f'\\1"{new}"', text, count=1, flags=re.M)
    )

    man = json.load(open(MANIFEST))
    man["version"] = new
    with open(MANIFEST, "w") as f:
        json.dump(man, f, indent=2)
        f.write("\n")

    changelog = open(CHANGELOG).read()
    # Guard on the heading: a retry that re-runs against an ALREADY-bumped tree
    # must not stack a second identical section.
    if f"\n## {new}\n" not in changelog:
        idx = changelog.index("\n## ")
        entry = changelog_entry(new, commit_subject)
        open(CHANGELOG, "w").write(changelog[:idx] + entry + changelog[idx:])

    return old, new


def main() -> int:
    old, new = apply(os.environ.get("COMMIT_MSG", ""))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"old_version={old}\nnew_version={new}\n")
    # Also written somewhere the SAME step can read: the retry loop needs the
    # versions to build its commit message, and a step cannot see its own
    # GITHUB_OUTPUT. Parsing them back out of the YAML with sed was the
    # alternative, and it was unreadable.
    env_file = os.environ.get("BUMP_ENV_FILE")
    if env_file:
        with open(env_file, "w") as f:
            f.write(f"OLD_VERSION={old}\nNEW_VERSION={new}\n")
    print(f"Bumped {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
