#!/usr/bin/env python3
"""Fail if a GitHub Actions workflow does not parse as YAML.

This exists for one specific, embarrassing reason: a workflow step named

    - name: smoke suite (fast: no network generations)

is a plain YAML scalar, so the `: ` inside the parentheses ends the scalar and
the parser reports "mapping values are not allowed here". GitHub accepts the
push, starts the workflow, and fails it in ~0 seconds with no usable log — so
the repo just looks broken. One unquoted colon is all it takes.

The same failure mode hits SKILL.md frontmatter (see lint_skills.py), which is
why both files exist.

Usage:  python3 packaging/lint_workflows.py [repo-root]
Exit:   0 clean (or pyyaml missing), 1 broken workflow
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs pyyaml, local runs may not
    print("skip — pyyaml not installed (pip install pyyaml)")
    sys.exit(0)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    wf_dir = root / ".github" / "workflows"
    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    if not files:
        print(f"skip — no workflows in {wf_dir}")
        return 0

    bad = 0
    for path in files:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            problem = getattr(exc, "problem", str(exc).replace("\n", " "))
            print(f"FAIL {path.name}{where}: {problem}")
            hint = path.read_text(encoding="utf-8").splitlines()
            if mark is not None and mark.line < len(hint):
                print(f"       {hint[mark.line].strip()}")
                print('       hint: wrap the value in quotes — `- name: "text (like: this)"`')
            bad += 1
            continue

        if not isinstance(doc, dict) or "jobs" not in doc:
            print(f"FAIL {path.name}: no `jobs:` mapping at the top level")
            bad += 1
            continue

        # `on:` is a YAML boolean key trap: unquoted `on` parses as True in
        # YAML 1.1 libraries, which silently means the workflow never triggers.
        if "on" not in doc and True not in doc:
            print(f"FAIL {path.name}: no `on:` trigger")
            bad += 1
            continue

        jobs = ", ".join(str(j) for j in doc["jobs"])
        print(f"ok   {path.name}  jobs: {jobs}")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
