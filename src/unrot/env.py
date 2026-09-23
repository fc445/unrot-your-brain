"""Finding the project's `.env`, from wherever you happened to run the command.

`load_dotenv()` on its own searches upward from the current working directory,
which quietly does the wrong thing the moment you run `python -m unrot.resolver`
from somewhere other than the repo root. The failure is not an error -- it is
the tool silently falling back to the default model, or reporting no API key
while one is sitting in a file two directories up. Both are confusing in a way
that costs more than the few lines it takes to look in the right place.

So: the checkout's own `.env` first, then the CWD-upward search, and real
environment variables always win over both. That ordering is the useful one --
`UNROT_MODEL=... python -m unrot.resolver run` has to override the file, or
trying a different model means editing the file and remembering to change it
back.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The repo root in a source checkout: src/unrot/env.py -> up three.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Every variable the tools read, and what it does. Kept here rather than only
#: in `.env.example` so that `unrot.resolver env` can print it without parsing
#: a file that may not exist yet.
SETTINGS: dict[str, str] = {
    "OPENROUTER_API_KEY": "Your OpenRouter key. Also accepted as OPENAI_API_KEY.",
    "UNROT_MODEL": "Which model answers. Any id the endpoint serves.",
    "UNROT_BASE_URL": (
        "OpenAI-compatible endpoint. Point at a local server to keep transcripts"
        " off the network entirely."
    ),
    "UNROT_REASONING_EFFORT": (
        "low | medium | high, or empty to let the model decide. OpenRouter only."
        " Default low."
    ),
    "UNROT_MAX_TOKENS": (
        "Output ceiling per call, reasoning included. Default 8000; a call that"
        " hits it while thinking is asked once more to answer directly."
    ),
    "LANGSMITH_API_KEY": "Optional. Traces the calls if LANGSMITH_TRACING is set.",
}


def env_path() -> Path:
    """Where the project's `.env` lives, whether or not it exists yet."""
    return PROJECT_ROOT / ".env"


def load_env() -> list[Path]:
    """Load the project's `.env`, then any `.env` above the working directory.

    Returns the files that were actually read, so a caller can say where its
    configuration came from. Never overrides a variable already set in the real
    environment, and never raises: running without python-dotenv, or without any
    `.env` at all, is a supported way to use these tools.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:  # pragma: no cover - dotenv is optional
        return []

    loaded: list[Path] = []
    project = env_path()
    if project.is_file():
        load_dotenv(project)
        loaded.append(project)

    nearby = find_dotenv(usecwd=True)
    if nearby and Path(nearby).resolve() != project.resolve():
        load_dotenv(nearby)
        loaded.append(Path(nearby))
    return loaded


def describe() -> list[tuple[str, str, str]]:
    """(name, status, help) for each setting, for printing.

    Secrets are reported as set or unset and never echoed -- a `env` subcommand
    that prints your API key into a terminal you may be sharing is a worse
    problem than the one it solves.
    """
    out = []
    for name, help_text in SETTINGS.items():
        value = os.environ.get(name)
        if not value:
            status = "unset"
        elif name.endswith("_KEY"):
            status = f"set ({len(value)} chars)"
        else:
            status = value
        out.append((name, status, help_text))
    return out
