"""What the worker says about itself at startup (spec 10.5).

A worker keeps the code it started with, so an operator has to be able to tell a
running worker apart from the checkout: the build fingerprint the startup hook
logs is that answer, and it must survive a checkout git cannot read. No network,
no Redis.
"""

from __future__ import annotations

import logging
from typing import cast

import pytest
from arq.connections import ArqRedis
from worker.main import StartupCtx, build_fingerprint, on_shutdown, on_startup
from worker_fakes import FakeRedis

from slopolis_core.settings import get_settings


async def test_startup_logs_one_build_fingerprint(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Given a deployment starting a worker — without a gateway, the boot path a
    # test can take without any credentials
    monkeypatch.setenv("LITELLM_MASTER_KEY", "")
    get_settings.cache_clear()

    # When it starts
    ctx: StartupCtx = {"redis": cast("ArqRedis", FakeRedis())}
    with caplog.at_level(logging.INFO, logger="worker.main"):
        await on_startup(ctx)

    # Then it says once what code it is running, naming something rather than
    # starting up anonymously
    fingerprints = [
        record for record in caplog.records if "fingerprint" in record.getMessage()
    ]
    assert len(fingerprints) == 1
    assert fingerprints[0].getMessage().strip() != "worker build fingerprint:"

    await on_shutdown(ctx)


def test_a_worker_that_cannot_read_its_revision_still_names_its_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a worker with no git to ask — a copy deployed without one
    def _no_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr("worker.main.subprocess.run", _no_git)

    # When it builds its fingerprint
    fingerprint = build_fingerprint()

    # Then it reports the revision as unknown and dates the sources it is running,
    # rather than failing to start over one line of log
    assert "no git revision" in fingerprint
    assert "worker sources dated" in fingerprint
