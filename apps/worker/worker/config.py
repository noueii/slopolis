"""Worker-specific runtime configuration (spec 10.5).

ARQ concurrency, retry/backoff, and timeout knobs live here, separate from the
shared process settings in :mod:`slopolis_core.settings`. Every field has a
sensible default so importing this module never requires the environment to be
populated.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["WorkerConfig", "get_worker_config"]


class WorkerConfig(BaseSettings):
    """Tuning for the ARQ worker: pool size, retries, timeouts, thresholds."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    #: Jobs processed concurrently per worker process (global pool size).
    max_jobs: int = Field(default=10, alias="WORKER_MAX_JOBS")
    #: Hard per-job wall-clock timeout in seconds; ARQ aborts past this.
    job_timeout_s: int = Field(default=900, alias="WORKER_JOB_TIMEOUT_S")
    #: Maximum attempts per target, including the first (ARQ ``max_tries``).
    max_tries: int = Field(default=4, alias="WORKER_MAX_TRIES")
    #: Base seconds for the exponential retry backoff.
    retry_backoff_s: int = Field(default=15, alias="WORKER_RETRY_BACKOFF_S")
    #: Upper bound on the computed backoff so retries stay responsive.
    retry_backoff_cap_s: int = Field(default=600, alias="WORKER_RETRY_BACKOFF_CAP_S")
    #: Default severity at/above which findings get an inline comment.
    inline_severity_threshold: str = Field(
        default="warning", alias="WORKER_INLINE_SEVERITY_THRESHOLD"
    )
    #: Timeout for outbound HTTP calls made by the worker's own clients.
    http_timeout_s: float = Field(default=30.0, alias="WORKER_HTTP_TIMEOUT_S")

    def backoff_seconds(self, attempt: int) -> int:
        """Return the exponential backoff delay for a 1-based ``attempt``.

        ``attempt=1`` yields the base delay; each further attempt doubles it up
        to :attr:`retry_backoff_cap_s`.
        """
        exponent = max(attempt - 1, 0)
        delay = self.retry_backoff_s * (2**exponent)
        return min(delay, self.retry_backoff_cap_s)


@lru_cache
def get_worker_config() -> WorkerConfig:
    """Return the process-wide worker config singleton (cache-clearable)."""
    return WorkerConfig()
