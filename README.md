# OcrPlane CLI

Agent-friendly command line client for the OcrPlane/MineRU OCR API.

`ocrplane-cli` is the installable package and Docker image name. The installed
command is `ocrplane`.

## Features

- Typer command line interface
- Pydantic v2 input and output models
- Rich human-readable terminal output
- Stable `--json` output for agents
- `--dry-run` request planning
- Async submit plus polling and paginated result reads

## Requirements

- Python 3.11+
- An OcrPlane API base URL
- An API key from OcrPlane

## Online Install

Recommended isolated install with `pipx`:

```bash
pipx install "git+https://github.com/asharca/ocrplane-cli.git"
ocrplane --help
```

If the repository is private or you prefer SSH:

```bash
pipx install "git+ssh://git@github.com/asharca/ocrplane-cli.git"
ocrplane --help
```

Upgrade later:

```bash
pipx upgrade ocrplane-cli
```

Install into the current Python environment with `pip`:

```bash
python3 -m pip install "git+https://github.com/asharca/ocrplane-cli.git"
ocrplane --help
```

Run directly with Docker:

```bash
docker run --rm \
  -e OCRPLANE_BASE_URL="https://ocr.rhzy.ai" \
  -e OCRPLANE_API_KEY="mk_xxxxxxxxxxxxxxxxxxxx" \
  -v "$PWD:/workspace" \
  ghcr.io/asharca/ocrplane-cli:latest \
  parse /workspace/report.pdf --json
```

## macOS Install

Using the system Python or Homebrew Python:

```bash
cd ~/Code/ocrplane-cli
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
ocrplane --help
```

If `python3` is missing:

```bash
brew install python
```

Optional isolated install with `pipx`:

```bash
brew install pipx
pipx ensurepath
pipx install "git+https://github.com/asharca/ocrplane-cli.git"
ocrplane --help
```

## Linux Install

Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git
cd ~/code/ocrplane-cli
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
ocrplane --help
```

RHEL/CentOS/Fedora:

```bash
sudo dnf install -y python3 python3-pip git
cd ~/code/ocrplane-cli
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
ocrplane --help
```

Optional isolated install with `pipx`:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install "git+https://github.com/asharca/ocrplane-cli.git"
ocrplane --help
```

## Configure

Set the API endpoint and key:

```bash
export OCRPLANE_BASE_URL="https://ocr.rhzy.ai"
export OCRPLANE_API_KEY="mk_xxxxxxxxxxxxxxxxxxxx"
```

Compatibility aliases are also supported:

```txt
MINERU_API_BASE_URL
MINERU_BASE_URL
MINERU_API_KEY
API_KEY
APIKEY
```

Prefer `OCRPLANE_BASE_URL` and `OCRPLANE_API_KEY` for new setups.

To keep local secrets out of git:

```bash
cp .env.example .env
```

Then edit `.env` and load it before using the CLI:

```bash
set -a
source .env
set +a
```

## Quick Start

Validate a request without sending it:

```bash
ocrplane parse /workspace/report.pdf --json --dry-run
```

Submit a document and wait for completion:

```bash
ocrplane parse /workspace/report.pdf --json
```

For large documents, submit first and read results by page:

```bash
ocrplane parse /workspace/large.pdf --json --no-wait
ocrplane status <task_id> --json
ocrplane markdown <task_id> --json --offset 0 --max-length 12000
ocrplane blocks <task_id> --json --offset 0 --limit 50
```

Write result artifacts to disk:

```bash
ocrplane parse /workspace/report.pdf --save-dir /workspace/ocr-report
```

This writes:

- `summary.json`
- `result.md`
- `content_blocks.json`
- `pages.json`

## Commands

```bash
ocrplane parse FILE
ocrplane status TASK_ID
ocrplane markdown TASK_ID
ocrplane blocks TASK_ID
ocrplane result TASK_ID
ocrplane list
ocrplane reprocess TASK_ID
ocrplane settings
```

Common parse options:

```bash
ocrplane parse /workspace/a.pdf \
  --backend pipeline \
  --lang ch \
  --parse-method auto \
  --formula \
  --table \
  --start-page 0 \
  --end-page 9 \
  --timeout 900 \
  --poll-interval 3 \
  --json
```

## Docker

Build locally:

```bash
docker build -t ocrplane-cli .
```

Run against a mounted workspace:

```bash
docker run --rm \
  -e OCRPLANE_BASE_URL \
  -e OCRPLANE_API_KEY \
  -v "$PWD:/workspace" \
  ocrplane-cli parse /workspace/report.pdf --json
```

Published images use:

```txt
ghcr.io/asharca/ocrplane-cli:latest
```

The GitHub Actions workflow publishes this image on pushes to `main`.

## Agent Notes

- Use `--json` for machine-readable output.
- Use `--dry-run` when planning a call or checking paths.
- Use `--no-wait` for large files, then poll with `status`.
- Read large markdown with `markdown --offset --max-length`.
- Read structured OCR blocks with `blocks --offset --limit`.
- Avoid printing API keys in logs or prompts.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m compileall src/ocrplane
python -m pip wheel . --no-deps -w /tmp/ocrplane-cli-wheel
```
