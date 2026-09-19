"""Tests for `.codereview.yml` parsing (spec §11)."""

import pytest

from slopolis_core.config.repo_config import (
    RepoConfigError,
    parse_repo_config,
)
from slopolis_core.domain import Severity


def test_empty_text_yields_defaults() -> None:
    """Given empty text, defaults are returned and review stays enabled."""
    result = parse_repo_config("")

    assert result.config.enabled is True
    assert result.config.version == 1
    assert result.config.review.severity_threshold is Severity.WARNING
    assert result.warnings == []


def test_null_text_yields_defaults() -> None:
    """Given a YAML null document, defaults are returned."""
    result = parse_repo_config("null\n")

    assert result.config.enabled is True


def test_full_config_parses() -> None:
    """Given a full config, every field is read from the YAML."""
    text = (
        "version: 1\n"
        "enabled: false\n"
        "review:\n"
        "  severity_threshold: error\n"
        "  max_files: 10\n"
        "  max_diff_lines: 500\n"
        "  ignore:\n"
        '    paths: ["**/*.lock"]\n'
        "  instructions: |\n"
        "    Follow house style.\n"
        "output:\n"
        "  inline_comments: false\n"
        "  summary_comment: true\n"
        "  check_run: false\n"
        "  suggestions: false\n"
        "  fail_check_on: critical\n"
    )

    result = parse_repo_config(text)

    assert result.config.enabled is False
    assert result.config.review.severity_threshold is Severity.ERROR
    assert result.config.review.max_files == 10
    assert result.config.review.max_diff_lines == 500
    assert result.config.review.ignore.paths == ["**/*.lock"]
    assert "Follow house style." in result.config.review.instructions
    assert result.config.output.inline_comments is False
    assert result.config.output.fail_check_on is Severity.CRITICAL
    assert result.warnings == []


def test_unknown_key_warns_but_parses() -> None:
    """Given an unknown key, a warning is emitted and the config still parses."""
    result = parse_repo_config("version: 1\nunknown_key: 2\n")

    assert result.config.version == 1
    assert any("unknown_key" in warning for warning in result.warnings)


def test_unknown_nested_key_warns() -> None:
    """Given an unknown nested key, a warning is emitted."""
    result = parse_repo_config("review:\n  mystery: true\n")

    assert any("mystery" in warning for warning in result.warnings)


def test_invalid_yaml_raises() -> None:
    """Given malformed YAML, a typed error is raised."""
    with pytest.raises(RepoConfigError):
        parse_repo_config("review: [unclosed")


def test_invalid_type_raises() -> None:
    """Given an invalid field type, a typed error is raised."""
    with pytest.raises(RepoConfigError):
        parse_repo_config("enabled: not-a-bool\n")


def test_invalid_severity_raises() -> None:
    """Given an unknown severity, a typed error is raised."""
    with pytest.raises(RepoConfigError):
        parse_repo_config("review:\n  severity_threshold: catastrophic\n")


def test_non_mapping_top_level_raises() -> None:
    """Given a non-mapping document, a typed error is raised."""
    with pytest.raises(RepoConfigError):
        parse_repo_config("- just\n- a\n- list\n")
