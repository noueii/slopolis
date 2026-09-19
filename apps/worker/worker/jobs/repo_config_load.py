"""Fetch and parse `.codereview.yml` for a review target (spec 10.7).

A missing config file yields the worker's default config; an unparseable file
raises :class:`PermanentTargetError` so the target fails without retrying,
while other targets in the session are unaffected.
"""

from __future__ import annotations

from slopolis_core.config.repo_config import (
    RepoConfig,
    RepoConfigError,
    ReviewConfig,
    parse_repo_config,
)
from slopolis_core.domain import Severity
from slopolis_core.github.errors import GitHubNotFoundError
from worker.config import WorkerConfig
from worker.deps import ContextReader

__all__ = ["CONFIG_PATH", "PermanentTargetError", "default_repo_config", "load_repo_config"]

CONFIG_PATH = ".codereview.yml"


class PermanentTargetError(RuntimeError):
    """A failure that cannot succeed on retry (e.g. invalid `.codereview.yml`)."""


async def load_repo_config(
    reader: ContextReader, repo_full_name: str, ref: str, config: WorkerConfig
) -> RepoConfig:
    """Fetch and parse `.codereview.yml`, or return the worker default config."""
    try:
        text = await reader.read_file(repo_full_name, CONFIG_PATH, ref)
    except GitHubNotFoundError:
        return default_repo_config(config)
    try:
        return parse_repo_config(text).config
    except RepoConfigError as exc:
        raise PermanentTargetError(str(exc)) from exc


def default_repo_config(config: WorkerConfig) -> RepoConfig:
    """Build a repo config whose threshold is the worker's configured default."""
    threshold = Severity(config.inline_severity_threshold)
    return RepoConfig(review=ReviewConfig(severity_threshold=threshold))
