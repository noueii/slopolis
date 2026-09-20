"""In-memory fake clients for the worker job tests.

Kept in a uniquely named module (not ``conftest``) so pyright resolves it via
the repo's ``extraPaths`` without colliding with other test suites' conftest.
No network is used, and no Redis: :class:`FakeRedis` is the sorted-set subset the
slot gate talks to.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from worker.credentials import ResolvedCredential

from slopolis_core.context import PrContext
from slopolis_core.github.errors import GitHubNotFoundError
from slopolis_core.github.models import GitHubPullRequest, InlineComment
from slopolis_core.llm.models import ChatMessage, CompletionResult

FINDING_PATH = "src/a.py"
HEAD_SHA = "true-head-sha"
HEAD_BRANCH = "feature/true-head"
REPO_FULL_NAME = "acme/api"
PR_NUMBER = 42
CATALOG_MODEL = "gpt-4o-mini"
CATALOG_PROVIDER = "openai"
#: The workspace credential a test links to the catalog model when it wants one.
CREDENTIAL_BASE_URL = "https://byok.example"
CREDENTIAL_KEY = "sk-workspace-credential"

__all__ = [
    "CATALOG_MODEL",
    "CATALOG_PROVIDER",
    "CREDENTIAL_BASE_URL",
    "CREDENTIAL_KEY",
    "FINDING_PATH",
    "HEAD_BRANCH",
    "HEAD_SHA",
    "PR_NUMBER",
    "REPO_FULL_NAME",
    "FakeCredentialClients",
    "FakeLlm",
    "FakePublisher",
    "FakeReader",
    "FakeRedis",
]


class FakeCredentialClients:
    """The credential-client seam: records each credential it is handed.

    Returns a fresh :class:`FakeLlm` per credential so a test can tell the
    credential's client from the fallback gateway by which one was asked to
    review, and its ``built`` pairs are the base URL and key each client was
    built from.
    """

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.built: list[tuple[str, str]] = []
        self.llms: list[FakeLlm] = []

    def __call__(self, credential: ResolvedCredential) -> FakeLlm:
        self.built.append((credential.base_url, credential.api_key))
        llm = FakeLlm(list(self._responses))
        self.llms.append(llm)
        return llm


class FakeRedis:
    """An in-memory stand-in for the Redis sorted sets the slot gate uses.

    Members are scores, keyed by dimension, the way Redis stores them; every
    command is counted so a test can prove an unlimited workspace never reaches
    Redis at all.
    """

    def __init__(self) -> None:
        self.sets: dict[str, dict[str, float]] = {}
        self.calls = 0

    def members(self, name: str) -> dict[str, float]:
        """Return the raw members of one key (the gate's hold bookkeeping)."""
        return dict(self.sets.get(name, {}))

    async def zadd(self, name: str, mapping: Mapping[str, float]) -> int:
        self.calls += 1
        members = self.sets.setdefault(name, {})
        added = 0
        for member, score in mapping.items():
            added += 1 if member not in members else 0
            members[member] = score
        return added

    async def zcard(self, name: str) -> int:
        self.calls += 1
        return len(self.sets.get(name, {}))

    async def zrem(self, name: str, *values: str) -> int:
        self.calls += 1
        members = self.sets.get(name, {})
        removed = 0
        for value in values:
            removed += 1 if members.pop(value, None) is not None else 0
        return removed

    async def zremrangebyscore(self, name: str, min: float, max: float) -> int:
        self.calls += 1
        members = self.sets.get(name, {})
        stale = [member for member, score in members.items() if min <= score <= max]
        for member in stale:
            del members[member]
        return len(stale)

    async def pexpire(self, name: str, time_ms: int) -> bool:
        self.calls += 1
        # Membership never expires here: the gate's dead-hold recovery is driven
        # by member scores, which a test can wind back the way a dying worker
        # would have left them.
        return bool(self.sets.get(name))


class FakeLlm:
    """Returns queued texts in order and records the models it was asked for."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.models: list[str] = []
        #: Set by :meth:`aclose`, the way a real client records its pool closing.
        self.closed = False

    async def aclose(self) -> None:
        """Record that the connection pool this client owns was shut down."""
        self.closed = True

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        self.models.append(model)
        text = self._responses.pop(0)
        return CompletionResult(
            text=text,
            model=model,
            provider=CATALOG_PROVIDER,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.002,
        )


class FakeReader:
    """Fake GitHub read surface; ``config_text=None`` means the file is absent."""

    def __init__(
        self,
        *,
        config_text: str | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.config_text = config_text
        self.fail_with = fail_with

    async def get_pull_request(self, repo_full_name: str, number: int) -> GitHubPullRequest:
        if self.fail_with is not None:
            raise self.fail_with
        return GitHubPullRequest(
            repo_full_name=repo_full_name,
            private=True,
            number=number,
            title="Add login",
            url=f"https://github.com/{repo_full_name}/pull/{number}",
            head_branch=HEAD_BRANCH,
            base_branch="main",
            head_sha=HEAD_SHA,
            default_branch="main",
            body="",
            author_login="octocat",
            draft=False,
            changed_files=1,
            additions=1,
            deletions=0,
            updated_at="2024-01-01T00:00:00Z",
        )

    async def get_pr_context(self, repo_full_name: str, number: int) -> PrContext:
        return PrContext(
            repo_full_name=repo_full_name,
            number=number,
            title="Add login",
            body="",
            changed_files=[FINDING_PATH],
            diff="diff --git a/src/a.py b/src/a.py\n+boom",
        )

    async def read_file(self, repo_full_name: str, path: str, ref: str) -> str:
        if self.config_text is None:
            raise GitHubNotFoundError(f"{repo_full_name}:{path} not found")
        return self.config_text


@dataclass
class FakePublisher:
    """Records every publish call and returns deterministic ids.

    ``fail_with`` is the failure knob: set it to make every write raise, the way
    GitHub answers a refused or throttled request, and clear it mid-test to let a
    later attempt through.
    """

    summaries: list[tuple[str, int, str, int | None]] = field(default_factory=list)
    inlines: list[tuple[str, int, list[InlineComment], str]] = field(default_factory=list)
    checks: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    #: Every summary-comment lookup, in order, so a test can see the job asked.
    summary_lookups: list[tuple[str, int]] = field(default_factory=list)
    #: The comments this pull request already holds, keyed by the path and line
    #: they are anchored to and listed in the order they were posted — what the
    #: real publisher's reconciliation would adopt for that location.
    existing_inline: dict[tuple[str, int], list[int]] = field(default_factory=dict)
    #: Every reconciliation, in order: the comments the job asked about.
    reconciliations: list[tuple[str, int, list[InlineComment]]] = field(
        default_factory=list
    )
    #: When set, only reconciliation raises it — a listing GitHub refused, which
    #: the publish has to survive by posting every comment anyway (spec 10.7).
    reconcile_fail_with: Exception | None = None
    #: The summary comment an earlier publish left on the pull request. ``None``
    #: means there is none, which is what makes the publish create one.
    existing_summary_id: int | None = None
    #: When set, the lookup raises it instead of answering — a listing GitHub
    #: refused or throttled, which the publish has to survive (spec 10.7).
    lookup_fail_with: Exception | None = None
    #: When set, every publish method raises it instead of recording a call.
    fail_with: Exception | None = None
    #: When set, inline posting raises the error from the given post on — GitHub
    #: taking one comment of several and refusing the next, which is when the ids
    #: of the comments it already accepted must not be lost (spec 10.7).
    fail_inline_after: tuple[int, Exception] | None = None
    #: When set, only the check run raises it — the advisory surface an
    #: installation without ``checks: write`` cannot post (spec 10.7).
    check_fail_with: Exception | None = None
    #: The next id a posted comment is given, so one publish that posts several
    #: comments stamps each finding with its own id, the way GitHub would.
    next_inline_id: int = 201

    def _refuse(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def find_summary_comment(self, repo_full_name: str, number: int) -> int | None:
        self.summary_lookups.append((repo_full_name, number))
        if self.lookup_fail_with is not None:
            raise self.lookup_fail_with
        return self.existing_summary_id

    async def upsert_summary_comment(
        self, repo_full_name: str, number: int, body: str, existing_comment_id: int | None
    ) -> int:
        self._refuse()
        self.summaries.append((repo_full_name, number, body, existing_comment_id))
        return 101

    async def post_inline_comments(
        self, repo_full_name: str, number: int, comments: list[InlineComment], commit_id: str
    ) -> list[int]:
        self._refuse()
        if self.fail_inline_after is not None:
            threshold, error = self.fail_inline_after
            already_posted = sum(len(posted) for _, _, posted, _ in self.inlines)
            if already_posted >= threshold:
                raise error
        self.inlines.append((repo_full_name, number, comments, commit_id))
        created = [self.next_inline_id + index for index in range(len(comments))]
        self.next_inline_id += len(comments)
        return created

    async def reconcile_inline_comments(
        self, repo_full_name: str, number: int, comments: list[InlineComment]
    ) -> list[int | None]:
        """Adopt a seeded comment per path and line, in the order each was posted."""
        self.reconciliations.append((repo_full_name, number, comments))
        if self.reconcile_fail_with is not None:
            raise self.reconcile_fail_with
        self._refuse()
        available = {key: list(ids) for key, ids in self.existing_inline.items()}
        adopted: list[int | None] = []
        for comment in comments:
            listed = available.setdefault((comment.path, comment.line), [])
            adopted.append(listed.pop(0) if listed else None)
        return adopted

    async def upsert_check_run(
        self,
        repo_full_name: str,
        head_sha: str,
        *,
        conclusion: str,
        title: str,
        summary: str,
    ) -> int:
        if self.check_fail_with is not None:
            raise self.check_fail_with
        self._refuse()
        self.checks.append((repo_full_name, head_sha, conclusion, title, summary))
        return 303
