"""Shared fakes for the hermetic end-to-end review-flow test.

A single :class:`FakeGitHub` implements both GitHub boundaries the stack uses:
the pre-flight ``GitHubGateway`` port and the worker's ``ContextReader``, so
the same pull request and ``.codereview.yml`` are seen on both sides. The rest
of the fakes stand in for the LLM gateway, the live model check, the ARQ Redis
pool, and the publisher's network surface.

Nothing in this module touches the network, Redis, or a real database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from slopolis_core.context import PrContext
from slopolis_core.github.errors import GitHubNotFoundError
from slopolis_core.github.models import GitHubPullRequest, InlineComment
from slopolis_core.llm.models import ChatMessage, CompletionResult
from slopolis_core.preflight.models import PrReference, RepositoryRef

__all__ = [
    "CATALOG_MODEL",
    "CATALOG_PROVIDER",
    "FINDINGS_JSON",
    "GROUNDED_PATH",
    "HEAD_BRANCH",
    "HEAD_SHA",
    "MODEL_ID",
    "PROVIDER",
    "PR_NUMBER",
    "PR_URL",
    "REPO_CONFIG_PATH",
    "REPO_CONFIG_YML",
    "REPO_FULL_NAME",
    "UNGROUNDED_PATH",
    "FakeArqPool",
    "FakeGitHub",
    "FakeLiveCheck",
    "FakeLlm",
    "FakePublisher",
]

# --- the one pull request the whole flow revolves around --------------------

PR_URL = "https://github.com/acme/api/pull/42"
REPO_FULL_NAME = "acme/api"
PR_NUMBER = 42
HEAD_SHA = "true-head-sha"
HEAD_BRANCH = "feature/true-head"
MODEL_ID = "gpt-4o-mini"
PROVIDER = "openai"

#: Kept distinct from :data:`MODEL_ID` only for readability in assertions.
CATALOG_MODEL = MODEL_ID
CATALOG_PROVIDER = PROVIDER

#: The changed file a grounded finding must point at.
GROUNDED_PATH = "src/a.py"
#: A path the PR never touched: the harness must drop findings on it.
UNGROUNDED_PATH = "src/generated/other.py"

REPO_CONFIG_PATH = ".codereview.yml"
REPO_CONFIG_YML = """\
review:
  severity_threshold: warning
output:
  inline_comments: true
  summary_comment: true
  check_run: true
"""

#: One valid findings envelope: a grounded finding (kept, inline, failing) and
#: an ungrounded one (dropped by the harness before persistence).
FINDINGS_JSON = json.dumps(
    {
        "findings": [
            {
                "path": GROUNDED_PATH,
                "line": 3,
                "severity": "error",
                "category": "correctness",
                "message": "Grounding bug on a changed path",
                "suggestion": "Guard the input before use",
                "confidence": 0.92,
            },
            {
                "path": UNGROUNDED_PATH,
                "line": 7,
                "severity": "error",
                "category": "correctness",
                "message": "Ungrounded finding on an untouched path",
                "suggestion": None,
                "confidence": 0.5,
            },
        ]
    }
)


class FakeGitHub:
    """One in-memory GitHub for the pre-flight gateway and the worker reader."""

    def __init__(self, *, config_text: str | None = REPO_CONFIG_YML) -> None:
        self.config_text = config_text
        self.resolved_urls: list[str] = []
        #: One entry per access check: the repository, its visibility, the user,
        #: and the override pre-flight resolved for it (``None`` = spec rule).
        self.checked_access: list[tuple[str, bool, str, str | None]] = []

    # --- app.adapters.github.GitHubGateway port (pre-flight) ----------------

    async def resolve_pr(self, url: str) -> PrReference:
        """Resolve the one known PR URL; anything else is a lookup miss."""
        self.resolved_urls.append(url)
        if url != PR_URL:
            raise LookupError(url)
        return PrReference(
            url=PR_URL,
            repository=RepositoryRef(
                id="repo_acme_api",
                full_name=REPO_FULL_NAME,
                private=False,
                default_branch="main",
            ),
            number=PR_NUMBER,
            title="Add login guard",
        )

    async def list_covered_repos(self) -> list[str]:
        """The installation covers exactly the one repository."""
        return [REPO_FULL_NAME]

    async def user_has_access(
        self,
        repo_full_name: str,
        *,
        private: bool,
        user_login: str,
        required: str | None = None,
    ) -> bool:
        """The triggering user may review the covered repository."""
        self.checked_access.append((repo_full_name, private, user_login, required))
        return repo_full_name == REPO_FULL_NAME

    async def read_repo_file(self, repo_full_name: str, path: str) -> str | None:
        """Serve ``.codereview.yml`` at the default branch, or no file."""
        if repo_full_name == REPO_FULL_NAME and path == REPO_CONFIG_PATH:
            return self.config_text
        return None

    async def publish_permissions(self, repo_full_name: str) -> dict[str, str]:
        """Report an installation that can write everything publishing needs.

        The fake models a correctly configured App: pre-flight refuses a
        submission whose installation cannot write (spec 10.3), and this flow is
        about what happens *after* that gate.
        """
        return {"pull_requests": "write", "issues": "write", "checks": "write"}

    # --- worker.deps.ContextReader port (review harness) --------------------

    async def get_pull_request(self, repo_full_name: str, number: int) -> GitHubPullRequest:
        """Return the PR metadata the worker re-reads before reviewing."""
        return GitHubPullRequest(
            repo_full_name=repo_full_name,
            private=False,
            number=number,
            title="Add login guard",
            url=PR_URL,
            head_branch=HEAD_BRANCH,
            base_branch="main",
            head_sha=HEAD_SHA,
            default_branch="main",
            body="",
            author_login="octocat",
            draft=False,
            changed_files=1,
            additions=12,
            deletions=1,
            updated_at="2024-01-01T00:00:00Z",
        )

    async def get_pr_context(self, repo_full_name: str, number: int) -> PrContext:
        """Return a diff whose changed files ground exactly the kept finding."""
        return PrContext(
            repo_full_name=repo_full_name,
            number=number,
            title="Add login guard",
            body="",
            changed_files=[GROUNDED_PATH],
            diff="diff --git a/src/a.py b/src/a.py\n+guard the input\n",
        )

    async def read_file(self, repo_full_name: str, path: str, ref: str) -> str:
        """Serve ``.codereview.yml`` at ``ref``; every other read is a 404."""
        if (
            repo_full_name == REPO_FULL_NAME
            and path == REPO_CONFIG_PATH
            and self.config_text is not None
        ):
            return self.config_text
        raise GitHubNotFoundError(f"{repo_full_name}:{path}@{ref} not found")


class FakeLlm:
    """Returns queued completion texts and records the models it was asked for."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.models: list[str] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        """Pop the next queued text and report fixed, non-zero usage."""
        self.models.append(model)
        text = self._responses.pop(0)
        return CompletionResult(
            text=text,
            model=model,
            provider=PROVIDER,
            prompt_tokens=120,
            completion_tokens=48,
            total_tokens=168,
            cost_usd=0.0123,
        )


class FakeLiveCheck:
    """A live model check that always answers ``None`` (success) and records calls."""

    def __init__(self) -> None:
        self.models: list[str] = []

    async def check(self, model: str) -> None:
        """Record the model that was pinged; never raises."""
        self.models.append(model)


class FakeArqPool:
    """Records enqueued jobs instead of touching Redis."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[str, ...]]] = []

    async def enqueue_job(self, function: str, *args: str) -> None:
        """Record one ``(function, args)`` pair."""
        self.jobs.append((function, args))


@dataclass
class FakePublisher:
    """Records every publish call and returns deterministic ids."""

    summaries: list[tuple[str, int, str, int | None]] = field(default_factory=list)
    inlines: list[tuple[str, int, list[InlineComment], str]] = field(default_factory=list)
    checks: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    #: A test sets this to the summary comment an earlier publish left on the
    #: pull request; ``None`` — the default — is a pull request with none yet.
    existing_summary_id: int | None = None

    async def find_summary_comment(self, repo_full_name: str, number: int) -> int | None:
        """Answer with the seeded existing comment, or ``None`` on a first publish."""
        return self.existing_summary_id

    async def upsert_summary_comment(
        self, repo_full_name: str, number: int, body: str, existing_comment_id: int | None
    ) -> int:
        """Record the rolling summary comment and return its id."""
        self.summaries.append((repo_full_name, number, body, existing_comment_id))
        return 101

    async def post_inline_comments(
        self, repo_full_name: str, number: int, comments: list[InlineComment], commit_id: str
    ) -> list[int]:
        """Record the inline comments and return one id per comment."""
        self.inlines.append((repo_full_name, number, comments, commit_id))
        return [201 + index for index in range(len(comments))]

    async def upsert_check_run(
        self,
        repo_full_name: str,
        head_sha: str,
        *,
        conclusion: str,
        title: str,
        summary: str,
    ) -> int:
        """Record the check run and return its id."""
        self.checks.append((repo_full_name, head_sha, conclusion, title, summary))
        return 303
