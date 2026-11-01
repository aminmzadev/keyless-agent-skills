#!/usr/bin/env python3
"""make_package.py — write package.json, the Claude plugin manifest and fix doc paths.

Called by build.sh. Kept in Python (not bash heredoc) because it rewrites markdown
paths with regexes and that is not a job for sed.
"""
import json
import os
import re
import shutil
import sys

SKILLS = ["skill-router", "anon-image", "anon-audio", "ffmpeg-pipeline",
          "web-search", "fetch-page", "deep-research"]


def rewrite_docs(out):
    """Docs are written for a flat checkout (web-search/scripts/…). Inside the
    npm package the same files live under skills/web-search/scripts/…, so fix
    the commands rather than making the reader guess."""
    for name in ("GUIDE.md", "README.md"):
        path = os.path.join(out, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        for s in SKILLS:
            text = re.sub(rf"(?<![./\w-]){s}/scripts/", f"skills/{s}/scripts/", text)
            text = re.sub(rf"\[`{s}`\]\({s}/SKILL\.md\)",
                          f"[`{s}`](skills/{s}/SKILL.md)", text)
            text = re.sub(rf"(?<![./\w-]){s}/SKILL\.md", f"skills/{s}/SKILL.md", text)
        text = text.replace("shared/cachectl.py", "skills/_shared/cachectl.py")
        text = text.replace("./test-all.sh", "# tests live in the repo, not the package")
        open(path, "w", encoding="utf-8").write(text)


def rewrite_manifest(out):
    """Inside the package every skill lives under skills/. MANIFEST.json's entry
    paths drive the router's output, so they have to describe the package, not
    the flat repo — otherwise `route.py` prints a command that does not exist."""
    path = os.path.join(out, "MANIFEST.json")
    d = json.load(open(path, encoding="utf-8"))
    for s in d.get("skills", []):
        if not s.get("entry", "").startswith("skills/"):
            s["entry"] = "skills/" + s["entry"]
        if s.get("secondary_entries"):
            s["secondary_entries"] = ["skills/" + e if not e.startswith("skills/") else e
                                      for e in s["secondary_entries"]]
    ep = {}
    for k, v in (d.get("entry_points") or {}).items():
        if isinstance(v, str):
            v = v.replace("./shared/", "skills/_shared/").replace("./skill-", "./skills/skill-")
        ep[k] = v
    d["entry_points"] = ep
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
        f.write("\n")


def main():
    src, out, owner, name, version = sys.argv[1:6]
    owner, name = owner.lower(), name.strip()

    rewrite_docs(out)
    rewrite_manifest(out)

    pkg = {
        "name": name,
        "version": version,
        "description": ("Seven key-less agent skills in one package: image generation and editing, "
                        "text-to-speech (online + fully offline), ffmpeg video pipelines, "
                        "multi-provider web search, URL-to-markdown, deep research with citations, "
                        "and an intent router. No API keys, no accounts, no sign-ups."),
        "keywords": ["agent-skills", "skills", "pi-package", "claude-code", "codex", "cursor",
                     "agentskills", "ai-agent", "text-to-speech", "tts", "piper",
                     "image-generation", "web-search", "deep-research", "ffmpeg", "no-api-key"],
        "license": "MIT",
        "author": owner,
        "homepage": f"https://github.com/{owner}/{name}#readme",
        "repository": {"type": "git", "url": f"git+https://github.com/{owner}/{name}.git"},
        "bugs": {"url": f"https://github.com/{owner}/{name}/issues"},
        "engines": {"node": ">=18"},
        # the scripts are POSIX python3 + ffmpeg; Windows needs WSL or a bash layer,
        # so say so instead of shipping something that half-works there
        "os": ["linux", "darwin"],
        "files": ["skills", "GUIDE.md", "README.md", "MANIFEST.json", "install.sh",
                  "LICENSE", "NOTICE", "DISCLOSURE"],
        # pi reads `pi.skills`; `skills` is what `npx skills experimental_sync` looks at
        "pi": {"skills": "./skills"},
        "skills": ["./skills"],
        "publishConfig": {"access": "public"},
    }
    with open(os.path.join(out, "package.json"), "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2)
        f.write("\n")

    shared = os.path.join(out, "skills", "_shared")
    os.makedirs(shared, exist_ok=True)
    shutil.copy(os.path.join(src, "shared", "cachectl.py"),
                os.path.join(shared, "cachectl.py"))

    market = {
        "metadata": {"pluginRoot": "."},
        "plugins": [{
            "name": "agent-skills",
            "source": ".",
            "description": ("Seven key-less agent skills: media generation, TTS (online + offline), "
                            "video, search, fetch, research, routing."),
            "skills": [f"./skills/{s}" for s in SKILLS],
        }],
    }
    with open(os.path.join(out, ".claude-plugin", "marketplace.json"), "w", encoding="utf-8") as f:
        json.dump(market, f, indent=2)
        f.write("\n")

    print(f"package.json  {name}@{version}  (github: {owner}/{name})")


if __name__ == "__main__":
    main()
