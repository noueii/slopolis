"""Typed API errors and the handlers that render them.

Every non-2xx response — including FastAPI's own 404 and validation failures —
uses the envelope defined by ``ApiErrorBody``::

    {"error": {"code": "...", "message": "...", "detail": "..."}}

so the UI's single ``ApiError`` path in ``apps/web/src/api/client.ts`` works
against real and mocked responses alike.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = ["ApiError", "install_error_handlers"]


class ApiError(Exception):
    """An expected API failure with a stable machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def _envelope(code: str, message: str, detail: str | None = None) -> dict[str, Any]:
    """Build the standard error body, omitting an absent detail."""
    error: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"error": error}


async def _api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render an :class:`ApiError` with its declared status and code."""
    error = exc if isinstance(exc, ApiError) else _unexpected_error()
    return JSONResponse(
        status_code=error.status_code,
        content=_envelope(error.code, error.message, error.detail),
    )


async def _validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI validation failures inside the standard envelope."""
    detail = _format_validation(exc) if isinstance(exc, RequestValidationError) else str(exc)
    return JSONResponse(
        status_code=422,
        content=_envelope(
            "validation_error",
            "The request body or query parameters were invalid.",
            detail,
        ),
    )


async def _http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render Starlette/FastAPI HTTP errors inside the standard envelope."""
    if isinstance(exc, StarletteHTTPException):
        status_code = exc.status_code
        detail = str(exc.detail)
    else:
        status_code = 500
        detail = "An unexpected error occurred."
    return JSONResponse(
        status_code=status_code,
        content=_envelope(_code_for_status(status_code), detail),
    )


def _unexpected_error() -> ApiError:
    """Return the fallback error used when a handler receives an unexpected type."""
    return ApiError(500, "internal_error", "An unexpected error occurred.")


def _code_for_status(status_code: int) -> str:
    """Map a bare HTTP status onto a stable error code."""
    return {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
    }.get(status_code, f"http_{status_code}")


def _format_validation(exc: RequestValidationError) -> str:
    """Join Pydantic's loc/msg pairs into one readable detail string."""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "invalid"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts)


def install_error_handlers(app: FastAPI) -> None:
    """Register every error handler on ``app``."""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
