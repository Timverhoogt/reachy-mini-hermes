from __future__ import annotations

from fastapi.testclient import TestClient

import reachy_mini_hermes.main as main_module
from reachy_mini_hermes.config import AppConfig
from reachy_mini_hermes.main import ReachyMiniHermes


class FakeBluetoothService:
    def __init__(self) -> None:
        self.scanned_seconds = 0
        self.enabled: bool | None = None

    def _status(self) -> dict[str, object]:
        return {
            "adapter_available": True,
            "adapter_powered": True,
            "scan_active": False,
            "devices": [],
            "gamepad_enabled": bool(self.enabled),
            "gamepad_connected": False,
            "gamepad_name": "",
            "gamepad_path": "",
            "last_gamepad_action": "",
            "last_error": "",
        }

    def refresh(self) -> dict[str, object]:
        return self._status()

    def scan(self, *, seconds: int) -> dict[str, object]:
        if seconds == 5:
            raise RuntimeError("adapter blocked")
        self.scanned_seconds = seconds
        return self._status()

    def set_gamepad_enabled(self, enabled: bool) -> dict[str, object]:
        self.enabled = enabled
        return self._status()


def test_bluetooth_management_routes_are_bounded_and_persist_gamepad_opt_in(monkeypatch) -> None:
    app = ReachyMiniHermes(False)
    service = FakeBluetoothService()
    app._bluetooth = service  # type: ignore[assignment]
    saved: list[AppConfig] = []
    monkeypatch.setattr(main_module, "load_config", lambda: AppConfig())
    monkeypatch.setattr(main_module, "save_config", lambda config: saved.append(config))
    client = TestClient(app.settings_app)

    status = client.get("/api/bluetooth/status")
    assert status.status_code == 200
    assert status.json()["adapter_powered"] is True

    scan = client.post("/api/bluetooth/scan", json={"seconds": 7})
    assert scan.status_code == 200
    assert service.scanned_seconds == 7
    failed_scan = client.post("/api/bluetooth/scan", json={"seconds": 5})
    assert failed_scan.status_code == 503
    assert failed_scan.json()["detail"] == "adapter blocked"

    invalid = client.post("/api/bluetooth/pair", json={"address": "not-a-mac"})
    assert invalid.status_code == 422

    enabled = client.post("/api/bluetooth/gamepad", json={"enabled": True})
    assert enabled.status_code == 200
    assert service.enabled is True
    assert saved and saved[-1].gamepad_enabled is True


def test_gamepad_precision_actions_use_the_existing_safe_precision_runtime_path() -> None:
    class FakeRuntime:
        def __init__(self) -> None:
            self.precision: list[tuple[str, float]] = []

        def queue_precision_robot_action(self, axis: str, delta: float) -> None:
            self.precision.append((axis, delta))

    app = ReachyMiniHermes(False)
    runtime = FakeRuntime()
    app._runtime = runtime  # type: ignore[assignment]

    app._handle_gamepad_action("precision", "body_yaw", "5.0")
    app._handle_gamepad_action("precision", "body_yaw", "-5.0")
    app._handle_gamepad_action("precision", "center_base", "0.0")

    assert runtime.precision == [
        ("body_yaw", 5.0),
        ("body_yaw", -5.0),
        ("center_base", 0.0),
    ]
