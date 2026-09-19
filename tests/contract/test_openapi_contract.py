"""OpenAPI contract test.

The web app may only depend on the documented API surface, so this fetches
``/openapi.json`` over the ASGI transport (no server process, no network) and
pins both the advertised paths and the camelCase property names that
``apps/web/src/api/contract.ts`` relies on.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

import pytest_asyncio
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from pydantic.alias_generators import to_snake

REQUIRED_PATHS: tuple[str, ...] = (
    "/api/sessions",
    "/api/sessions/{session_id}",
    "/api/sessions/{session_id}/events",
    "/api/reviews/preflight",
    "/api/dashboard",
    "/api/repositories",
    "/api/models",
    "/api/presets",
    "/api/usage",
    "/api/me",
)

#: camelCase property -> the OpenAPI schema that must expose it.
CAMEL_PROPS: dict[str, str] = {
    "costUsd": "ReviewSession",
    "headBranch": "SessionTarget",
    "triggeredBy": "ReviewSession",
    "targetCount": "ReviewSession",
    "findingsCount": "SessionTarget",
    "pageSize": "Paginated",
    "isAdmin": "UserRef",
}


@pytest_asyncio.fixture
async def openapi() -> AsyncGenerator[dict[str, object]]:
    """Fetch the live OpenAPI document over the ASGI transport."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/openapi.json")
    assert response.status_code == 200
    yield cast("dict[str, object]", response.json())


async def test_openapi_advertises_every_required_path(
    openapi: dict[str, object],
) -> None:
    """Every path the UI calls is present in the generated schema."""
    paths = cast("dict[str, object]", openapi["paths"])
    missing = sorted(set(REQUIRED_PATHS) - set(paths))
    assert missing == [], f"OpenAPI is missing required paths: {missing}"


async def test_openapi_schemas_expose_camel_case_props(
    openapi: dict[str, object],
) -> None:
    """Key schemas expose camelCase props and never leak their snake_case form."""
    components = cast("dict[str, object]", openapi["components"])
    schemas = cast("dict[str, dict[str, object]]", components["schemas"])
    for prop, schema_name in CAMEL_PROPS.items():
        schema = schemas[schema_name]
        properties = cast("dict[str, object]", schema["properties"])
        assert prop in properties, f"{schema_name} is missing camelCase prop {prop!r}"
        snake = to_snake(prop)
        assert snake not in properties, f"{schema_name} leaks snake_case prop {snake!r}"
