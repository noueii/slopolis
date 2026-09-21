"""Tests for review prompt composition (spec 10.6)."""

from slopolis_core.config.repo_config import RepoConfig, ReviewConfig
from slopolis_core.context import PrContext
from slopolis_core.prompts.review import compose_review_prompt, compose_system_prompt


def _pr() -> PrContext:
    return PrContext(
        repo_full_name="acme/api-gateway",
        number=142,
        title="Guard token refresh skew",
        body="Fixes refresh race.",
        changed_files=["src/auth.py"],
        diff="@@ -1 +1 @@\n-old\n+new\n",
    )


def test_system_prompt_contains_json_contract_and_severities() -> None:
    """The built-in prompt states the exact JSON shape and severity set."""
    prompt = compose_system_prompt()

    assert '{"findings":[{"path","line","severity","category","message"' in prompt
    assert "info, warning, error, critical" in prompt


def test_system_prompt_requires_replacement_code_in_suggestion() -> None:
    """The built-in prompt says `suggestion` is code, never prose."""
    prompt = compose_system_prompt()

    assert "literal replacement" in prompt
    assert "no markdown fence" in prompt
    assert "put that advice in message instead" in prompt


def test_review_prompt_layers_instructions_then_session() -> None:
    """Repo instructions precede the session prompt in the composed prompt."""
    repo_config = RepoConfig(
        review=ReviewConfig(instructions="Follow house style. Flag N+1 queries.",),
    )

    prompt = compose_review_prompt(repo_config, _pr(), "Focus on the auth path.")

    assert "Follow house style." in prompt
    assert "Focus on the auth path." in prompt
    assert prompt.index("Follow house style.") < prompt.index("Focus on the auth path.")


def test_review_prompt_includes_pr_context() -> None:
    """The composed prompt carries the PR metadata and diff."""
    prompt = compose_review_prompt(RepoConfig(), _pr(), None)

    assert "acme/api-gateway#142" in prompt
    assert "Guard token refresh skew" in prompt
    assert "src/auth.py" in prompt


def test_review_prompt_builtin_first() -> None:
    """The built-in behavior is the first layer in the composed prompt."""
    repo_config = RepoConfig(review=ReviewConfig(instructions="Repo rules here."))

    prompt = compose_review_prompt(repo_config, _pr(), "Session rules here.")

    builtin = compose_system_prompt()
    assert prompt.startswith(builtin)
    assert prompt.index("Repo rules here.") < prompt.index("Session rules here.")


def test_review_prompt_skips_empty_session_prompt() -> None:
    """A blank session prompt adds no SESSION section."""
    prompt = compose_review_prompt(RepoConfig(), _pr(), "   ")

    assert "SESSION INSTRUCTIONS" not in prompt
