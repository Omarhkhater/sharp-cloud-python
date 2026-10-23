"""Test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import aiohttp.resolver
import pytest
from homeassistant.helpers import aiohttp_client

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "boxinfo.json"


@pytest.fixture
def boxinfo() -> dict:
    """Redacted boxInfo capture."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _load_integration(request, monkeypatch):
    """Point Home Assistant's test config at this repo when a test uses hass."""
    # This environment's aiodns build starts a thread Home Assistant's test
    # harness rejects. The integration still asks Home Assistant for the session.
    monkeypatch.setattr(aiohttp_client, "AsyncResolver", aiohttp.resolver.ThreadedResolver)
    if "hass" not in request.fixturenames:
        yield
        return
    hass = request.getfixturevalue("hass")
    request.getfixturevalue("enable_custom_integrations")
    destination = Path(hass.config.config_dir) / "custom_components" / "sharp_cocoro_air"
    source = Path(__file__).resolve().parents[1] / "custom_components" / "sharp_cocoro_air"
    if not destination.exists():
        destination.symlink_to(source)
    yield
