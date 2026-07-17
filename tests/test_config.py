from __future__ import annotations

import os
from pathlib import Path

import pytest

from reachy_mini_hermes.config import AppConfig, load_config, merge_config, save_config


def test_config_round_trip_and_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    original = AppConfig(bridge_url="http://hermes.local:8643", api_key="super-secret")
    save_config(original, path)

    loaded = load_config(path)
    assert loaded.bridge_url == "http://hermes.local:8643"
    assert loaded.api_key == "super-secret"
    assert loaded.instance_id == original.instance_id
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_redacted_config_never_exposes_secret() -> None:
    config = AppConfig(api_key="super-secret")
    payload = config.redacted_dict()
    assert payload["api_key"] == "********"
    assert payload["api_key_configured"] is True
    assert "super-secret" not in repr(payload)


def test_masked_key_keeps_existing_secret() -> None:
    current = AppConfig(api_key="keep-me")
    updated = merge_config(current, {"api_key": "********", "language": "nl"})
    assert updated.api_key == "keep-me"
    assert updated.language == "nl"


def test_embodiment_features_are_explicit_and_privacy_bounded_by_default() -> None:
    config = AppConfig()

    assert config.face_tracking_enabled is False
    assert config.face_tracking_weight == 0.65
    assert config.doa_enabled is False
    assert config.robot_tools_enabled is True


@pytest.mark.parametrize("weight", [-0.01, 1.01])
def test_face_tracking_weight_must_be_bounded(weight: float) -> None:
    with pytest.raises(ValueError, match="face_tracking_weight"):
        AppConfig(face_tracking_weight=weight)


@pytest.mark.parametrize("url", ["", "localhost:8643", "file:///tmp/socket"])
def test_bridge_url_must_be_http(url: str) -> None:
    with pytest.raises(ValueError):
        AppConfig(bridge_url=url)
