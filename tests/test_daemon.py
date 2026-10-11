"""Daemon FastAPI skeleton tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pboy.daemon.app import create_app


@pytest.fixture
def daemon_client(tmp_path: Path) -> TestClient:
    app = create_app(db_path=tmp_path / "daemon.db", secret_key="daemon-test-key")
    with TestClient(app) as client:
        yield client


def test_healthz_ok(daemon_client: TestClient) -> None:
    res = daemon_client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "pboy-daemon"
    assert "daemon.db" in body["db_path"]
