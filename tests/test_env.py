"""Tests for configuration loading.

Small surface, but the failure mode is nasty: every mistake here is silent. A
`.env` that is not found does not raise -- it leaves you talking to the default
model while believing you configured another one, and the only evidence is a
provenance string nobody reads until later. So the precedence is pinned down
here rather than left to be inferred from behaviour.
"""

from __future__ import annotations

import pytest

from unrot import env as env_module
from unrot.model import DEFAULT_MODEL, ModelConfig


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """No inherited configuration, so these test the code and not the machine."""
    for name in env_module.SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def write_env(directory, **values) -> None:
    (directory / ".env").write_text(
        "\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8"
    )


def test_the_model_can_be_set_in_the_env_file(tmp_path, monkeypatch):
    write_env(tmp_path, UNROT_MODEL="anthropic/claude-haiku-4-5")
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)

    env_module.load_env()
    assert ModelConfig.from_env().model == "anthropic/claude-haiku-4-5"


def test_the_project_env_is_found_from_any_working_directory(tmp_path, monkeypatch):
    """The whole reason this module exists.

    `load_dotenv()` alone searches upward from the CWD, so running the command
    from anywhere else silently falls back to defaults rather than failing.
    """
    project = tmp_path / "repo"
    project.mkdir()
    write_env(project, UNROT_MODEL="from/the-project")
    monkeypatch.setattr(env_module, "PROJECT_ROOT", project)

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    loaded = env_module.load_env()
    assert loaded == [project / ".env"]
    assert ModelConfig.from_env().model == "from/the-project"


def test_a_real_environment_variable_beats_the_file(tmp_path, monkeypatch):
    """Otherwise trying another model means editing a file and remembering to undo it."""
    write_env(tmp_path, UNROT_MODEL="from/the-file")
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("UNROT_MODEL", "from/the-environment")

    env_module.load_env()
    assert ModelConfig.from_env().model == "from/the-environment"


def test_an_explicit_argument_beats_everything(tmp_path, monkeypatch):
    """What `--model` on the command line resolves to."""
    write_env(tmp_path, UNROT_MODEL="from/the-file")
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)

    env_module.load_env()
    assert ModelConfig.from_env(model="from/the-flag").model == "from/the-flag"


def test_no_env_file_at_all_is_a_supported_way_to_run(tmp_path, monkeypatch):
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path / "nothing-here")
    monkeypatch.chdir(tmp_path)

    assert env_module.load_env() == []
    assert ModelConfig.from_env().model == DEFAULT_MODEL


def test_the_endpoint_can_be_pointed_at_a_local_server(tmp_path, monkeypatch):
    """The PRD's local-only-analysis option is this line, not a rewrite."""
    write_env(
        tmp_path,
        UNROT_BASE_URL="http://localhost:11434/v1",
        UNROT_MODEL="qwen2.5-coder",
    )
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)

    env_module.load_env()
    config = ModelConfig.from_env()
    assert config.base_url == "http://localhost:11434/v1"
    assert config.model == "qwen2.5-coder"


def test_the_configured_model_reaches_provenance(tmp_path, monkeypatch):
    """S5: changing the model in `.env` must be visible in recorded history.

    Otherwise switching models is indistinguishable from the user's world having
    changed, which is exactly the confusion the version strings exist to prevent.
    """
    from unrot.detector import detector_version
    from unrot.resolver import resolver_version

    write_env(tmp_path, UNROT_MODEL="anthropic/claude-haiku-4-5")
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)
    env_module.load_env()

    label = ModelConfig.from_env().label
    assert "claude-haiku-4-5" in detector_version(label)
    assert "claude-haiku-4-5" in resolver_version(label)


def test_describe_never_echoes_a_secret(tmp_path, monkeypatch):
    """`env` prints into a terminal that may be shared or recorded."""
    secret = "sk-or-v1-totally-secret-value"
    write_env(tmp_path, OPENROUTER_API_KEY=secret, UNROT_MODEL="a/model")
    monkeypatch.setattr(env_module, "PROJECT_ROOT", tmp_path)
    env_module.load_env()

    rendered = {name: status for name, status, _ in env_module.describe()}
    assert secret not in " ".join(rendered.values())
    assert rendered["OPENROUTER_API_KEY"] == f"set ({len(secret)} chars)"
    # A non-secret is shown in full, because being able to see it is the point.
    assert rendered["UNROT_MODEL"] == "a/model"
