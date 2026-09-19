"""Strict request/response models for synchronous pre-flight (spec 10.3).

Field names mirror ``apps/web/src/api/contract.ts`` one-to-one. Models are
camelCase on the wire and snake_case in Python via :func:`to_camel` plus
``populate_by_name``, so both spellings parse.
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

__all__ = [
    "PrReference",
    "PreflightOutcome",
    "PreflightRequest",
    "RepositoryRef",
]


class _WireModel(BaseModel):
    """Base for models that accept both camelCase and snake_case input."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class RepositoryRef(_WireModel):
    """A repository connected through the GitHub App (spec 10.1)."""

    id: str
    full_name: str
    private: bool
    default_branch: str | None = None


class PrReference(_WireModel):
    """One pull request resolved from a pasted link."""

    url: str
    repository: RepositoryRef
    number: int
    title: str


class PreflightRequest(_WireModel):
    """Data submitted by the New Review form."""

    pr_urls: list[str] = Field(default_factory=list)


class PreflightOutcome(_WireModel):
    """Result of synchronous pre-flight validation.

    ``valid`` holds everything the caller must queue; ``invalid`` holds link
    strings that failed validation; ``notices`` holds non-fatal observations
    (dedupes, unknown keys, cross-repo notes) and explaining failures.
    """

    valid: list[PrReference] = Field(default_factory=list)
    invalid: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
