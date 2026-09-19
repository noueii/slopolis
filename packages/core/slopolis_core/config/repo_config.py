"""`.codereview.yml` schema and parser (spec §11, Phase-1 subset).

A repo config can only express **intent** — never models, never keys. Invalid
YAML or invalid types fail (raising :class:`RepoConfigError`); unknown keys
**warn but do not fail**. Empty or `null` input yields defaults with review
enabled.
"""

from dataclasses import dataclass
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from slopolis_core.domain import Severity

__all__ = [
    "IgnoreConfig",
    "OutputConfig",
    "RepoConfig",
    "RepoConfigError",
    "RepoConfigParseResult",
    "ReviewConfig",
    "parse_repo_config",
]

_DEFAULT_IGNORE_PATHS: list[str] = [
    "**/*.lock",
    "dist/**",
    "**/*.min.js",
    "vendor/**",
]


class RepoConfigError(ValueError):
    """Raised when `.codereview.yml` is not valid YAML or has invalid types."""


class IgnoreConfig(BaseModel):
    """Paths excluded from review."""

    model_config = ConfigDict(extra="allow")

    paths: list[str] = list(_DEFAULT_IGNORE_PATHS)


class ReviewConfig(BaseModel):
    """Review behavior: threshold, caps, ignores, free-form instructions."""

    model_config = ConfigDict(extra="allow")

    severity_threshold: Severity = Severity.WARNING
    max_files: int = 50
    max_diff_lines: int = 20000
    ignore: IgnoreConfig = IgnoreConfig()
    instructions: str = ""


class OutputConfig(BaseModel):
    """Where and how findings are published back to GitHub."""

    model_config = ConfigDict(extra="allow")

    inline_comments: bool = True
    summary_comment: bool = True
    check_run: bool = True
    suggestions: bool = True
    fail_check_on: Severity = Severity.ERROR


class RepoConfig(BaseModel):
    """Root of `.codereview.yml`."""

    model_config = ConfigDict(extra="allow")

    version: int = 1
    enabled: bool = True
    review: ReviewConfig = ReviewConfig()
    output: OutputConfig = OutputConfig()


@dataclass(frozen=True, slots=True)
class RepoConfigParseResult:
    """Parsed config plus non-fatal warnings (unknown keys, etc.)."""

    config: RepoConfig
    warnings: list[str]


_KNOWN_ROOT_KEYS = {"version", "enabled", "review", "output"}
_KNOWN_SECTIONS: dict[str, set[str]] = {
    "review": {"severity_threshold", "max_files", "max_diff_lines", "ignore", "instructions"},
    "output": {
        "inline_comments",
        "summary_comment",
        "check_run",
        "suggestions",
        "fail_check_on",
    },
    "ignore": {"paths"},
}


def _collect_unknown_key_warnings(payload: dict[str, Any]) -> list[str]:
    """Return a warning per unrecognized key at the root and known sections."""
    warnings: list[str] = []
    for key in payload:
        if key not in _KNOWN_ROOT_KEYS:
            warnings.append(f"Unknown key '{key}' in .codereview.yml (ignored)")
    for section, known in _KNOWN_SECTIONS.items():
        section_value = payload.get(section)
        if not isinstance(section_value, dict):
            continue
        section_mapping = cast("dict[str, Any]", section_value)
        for key in section_mapping:
            if key not in known:
                warnings.append(
                    f"Unknown key '{key}' under '{section}' in .codereview.yml (ignored)"
                )
    return warnings


def parse_repo_config(text: str) -> RepoConfigParseResult:
    """Parse `.codereview.yml` text into a config plus warnings.

    Empty or `null` text yields defaults (``enabled=True``). Raises
    :class:`RepoConfigError` on invalid YAML or invalid field types.
    """
    if not text.strip():
        return RepoConfigParseResult(config=RepoConfig(), warnings=[])

    try:
        parsed: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RepoConfigError(f"Invalid YAML in .codereview.yml: {exc}") from exc

    if parsed is None:
        return RepoConfigParseResult(config=RepoConfig(), warnings=[])
    if not isinstance(parsed, dict):
        raise RepoConfigError(
            f".codereview.yml must be a mapping at the top level, got {type(parsed).__name__}"
        )

    payload = cast("dict[str, Any]", parsed)
    warnings = _collect_unknown_key_warnings(payload)

    try:
        config = RepoConfig.model_validate(payload)
    except ValidationError as exc:
        raise RepoConfigError(f"Invalid .codereview.yml: {exc}") from exc

    return RepoConfigParseResult(config=config, warnings=warnings)
