# Security

## What this pack does with your machine

- Runs Python scripts you invoke. No daemon, no background process, no file watcher.
- Writes only to the cache root (`$AGENT_SKILLS_CACHE`, default `~/.cache/agent-skills`)
  and to the output paths you pass on the command line.
- Makes outbound HTTP requests to third-party endpoints. It never listens on a port.
- Calls `ffmpeg`/`ffprobe` with fixed argument vectors. No `shell=True`.
- Stores no credentials, because it never has any.

## What it deliberately does not do

- No `postinstall`, `preinstall` or any npm lifecycle script.
- No binaries or models in the package; `install.sh --piper` and `tts.py fetch-voice`
  download those, once, at your request, printing what they do.
- No telemetry, no analytics, no phone-home.

## Reporting

Endpoints rot. If a skill starts failing in a way that looks like a *behaviour* change
rather than a network hiccup, run its `probe` subcommand and paste the output in an
issue:

```bash
python3 anon-image/scripts/imgen.py probe
python3 anon-audio/scripts/tts.py probe
python3 ffmpeg-pipeline/scripts/vidkit.py probe
python3 web-search/scripts/search.py --list-providers
```

For anything that looks like a real vulnerability (arbitrary file write, command
injection through a URL, credential leak), open an issue marked **security** or use
GitHub's private vulnerability reporting on the repository.
