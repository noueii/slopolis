"""Pull-request context passed into prompt composition (spec 10.6).

`PrContext` is the concrete shape the review harness will build once it can
call the GitHub API. `PrContextLike` is the structural protocol prompt
composition depends on, so tests and future providers can substitute any
object exposing the same attributes.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

__all__ = ["PrContext", "PrContextLike"]


class PrContext(BaseModel):
    """Everything the reviewer needs to know about one pull request."""

    model_config = ConfigDict(extra="forbid")

    repo_full_name: str
    number: int
    title: str
    body: str = ""
    changed_files: list[str] = []
    diff: str = ""


@runtime_checkable
class PrContextLike(Protocol):
    """Structural type for the PR context consumed by prompt composition."""

    @property
    def repo_full_name(self) -> str: ...

    @property
    def number(self) -> int: ...

    @property
    def title(self) -> str: ...

    @property
    def body(self) -> str: ...

    @property
    def changed_files(self) -> list[str]: ...

    @property
    def diff(self) -> str: ...
