"""Pytest fixtures."""
from __future__ import annotations

import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", help="run real S3 / network integration tests")
    parser.addoption("--live", action="store_true", help="run full live e2e tests")


@pytest.fixture
def integration(pytestconfig) -> bool:
    return pytestconfig.getoption("--integration")


@pytest.fixture
def live(pytestconfig) -> bool:
    return pytestconfig.getoption("--live")


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch):
    """Дефолтные env для большинства тестов чтобы Settings() не падал."""
    monkeypatch.setenv("TG_BOT_TOKEN", os.environ.get("TG_BOT_TOKEN", "test-tg-token"))
    monkeypatch.setenv("KIE_API_KEY", os.environ.get("KIE_API_KEY", "test-kie-key"))
    monkeypatch.setenv("S3_ACCESS_KEY", os.environ.get("S3_ACCESS_KEY", "test-ak"))
    monkeypatch.setenv("S3_SECRET_KEY", os.environ.get("S3_SECRET_KEY", "test-sk"))
