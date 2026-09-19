"""In-repo configuration (`.codereview.yml`, spec §11)."""

from slopolis_core.config.repo_config import (
    IgnoreConfig,
    OutputConfig,
    RepoConfig,
    RepoConfigError,
    RepoConfigParseResult,
    ReviewConfig,
    parse_repo_config,
)

__all__ = [
    "IgnoreConfig",
    "OutputConfig",
    "RepoConfig",
    "RepoConfigError",
    "RepoConfigParseResult",
    "ReviewConfig",
    "parse_repo_config",
]
