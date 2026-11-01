#!/usr/bin/env python3
"""
lint_skills.py — validate the frontmatter of every SKILL.md in the pack.

This exists because a real installer rejected one of them: the skills CLI
(`npx skills add`) refused fetch-page/SKILL.md with

    YAML parse error: Nested mappings are not allowed in compact mappings at
    line 2, column 14

because the description contained an unquoted `": "` ("…readable content:
documentation…"). Six of seven skills installed; one silently vanished. That is
exactly the class of bug a ten-line lint catches and nobody notices by eye.

Checks per skill directory:
  * SKILL.md exists and starts with `---`
  * the frontmatter parses as YAML (PyYAML if available, structural fallback if not)
  * `name` and `description` are present and non-empty
  * `name` equals the directory name (skills.sh and pi both rely on this)
  * `description` is under 1024 chars (pi truncates/scolds beyond that)
  * no unquoted `": "` inside a plain scalar value

Usage:
    python3 packaging/lint_skills.py [skills-root]     # default: repo root
Exit: 0 clean · 1 problems (printed one per line)
"""
import glob
import os
import re
import sys

LIMIT = 1024


def load_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        return None


def check_file(path, root):
    problems = []
    folder = os.path.basename(os.path.dirname(path))
    text = open(path, encoding="utf-8").read()

    if not text.startswith("---"):
        return [f"{folder}: no YAML frontmatter"]
    try:
        block = text.split("---", 2)[1]
    except IndexError:
        return [f"{folder}: unterminated frontmatter"]

    yaml = load_yaml()
    data = None
    if yaml is not None:
        try:
            data = yaml.safe_load(block) or {}
        except Exception as e:
            return [f"{folder}: YAML: {str(e).splitlines()[0][:100]}"]
        if not isinstance(data, dict):
            return [f"{folder}: frontmatter is not a mapping"]

    name = (data or {}).get("name")
    desc = (data or {}).get("description")

    if data is None:      # no PyYAML: structural checks only
        m_name = re.search(r"^name:\s*(\S+)", block, re.M)
        m_desc = re.search(r"^description:\s*(.+)$", block, re.M)
        name = m_name.group(1) if m_name else None
        desc = m_desc.group(1) if m_desc else None
        for line in block.splitlines():
            m = re.match(r"^([A-Za-z_]+):\s*(.+)$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if val.lstrip().startswith(('"', "'", ">", "|", "[", "{")):
                continue
            if ": " in val:
                problems.append(f"{folder}: unquoted ': ' in '{key}' breaks YAML parsers")

    if not name:
        problems.append(f"{folder}: missing 'name'")
    elif name != folder:
        problems.append(f"{folder}: name '{name}' != folder '{folder}'")
    if not desc:
        problems.append(f"{folder}: missing 'description'")
    elif isinstance(desc, str) and len(desc) > LIMIT:
        problems.append(f"{folder}: description is {len(desc)} chars (limit {LIMIT})")
    return problems


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    files = sorted(glob.glob(os.path.join(root, "*", "SKILL.md")))
    if not files:
        print(f"no SKILL.md under {root}", file=sys.stderr)
        return 1
    problems = []
    for f in files:
        problems += check_file(f, root)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"ok — {len(files)} skills, frontmatter valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
