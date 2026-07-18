from __future__ import annotations

import importlib.util
from pathlib import Path

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "companion" / "hermes_reachy_bridge.py"


def load_bridge_module():
    spec = importlib.util.spec_from_file_location("hermes_reachy_bridge", BRIDGE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_parser_and_api_key_resolution(tmp_path, monkeypatch) -> None:
    bridge = load_bridge_module()
    env_path = tmp_path / ".env"
    env_path.write_text('API_SERVER_KEY="from-file"\nIGNORED=value\n', encoding="utf-8")
    assert bridge._parse_env_file(env_path)["API_SERVER_KEY"] == "from-file"

    monkeypatch.setenv("API_SERVER_KEY", "from-env")
    assert bridge._resolve_api_key("", None) == "from-env"
    assert bridge._resolve_api_key("explicit", None) == "explicit"


def test_api_key_resolution_from_profile_config(tmp_path, monkeypatch) -> None:
    bridge = load_bridge_module()
    profile_home = tmp_path / "profiles" / "robot"
    profile_home.mkdir(parents=True)
    (profile_home / "config.yaml").write_text(
        "API_SERVER_KEY: from-config\n", encoding="utf-8"
    )
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert bridge._resolve_api_key("", "robot") == "from-config"


def test_create_app_routes_are_present() -> None:
    bridge = load_bridge_module()
    app = bridge.create_app(api_key="secret", hermes_url="http://127.0.0.1:8642")
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("POST", "/v1/chat/completions") in routes
    assert ("POST", "/v1/audio/transcriptions") in routes
    assert ("POST", "/v1/audio/speech") in routes
    assert ("GET", "/health") in routes
    assert ("GET", "/v1/models") in routes
    assert ("GET", "/v1/voice-options") in routes
    assert ("GET", "/v1/realtime") in routes


def test_realtime_robot_tools_are_curated_and_can_be_disabled() -> None:
    bridge = load_bridge_module()

    names = {tool["name"] for tool in bridge._build_realtime_tools(True, True)}
    assert names == {
        "ask_hermes",
        "set_reachy_power_mode",
        "capture_reachy_camera",
        "move_reachy_head",
        "express_reachy_emotion",
        "dance_reachy",
    }
    assert {tool["name"] for tool in bridge._build_realtime_tools(False, False)} == {
        "ask_hermes",
        "set_reachy_power_mode",
    }
    power_tool = next(
        tool for tool in bridge._build_realtime_tools(False, False) if tool["name"] == "set_reachy_power_mode"
    )
    assert power_tool["parameters"]["properties"]["mode"]["enum"] == [
        "standby",
        "awake",
        "meeting",
        "sleep",
    ]
    duration = power_tool["parameters"]["properties"]["duration_minutes"]
    assert duration["minimum"] == 1
    assert duration["maximum"] == 480


def test_ask_hermes_requires_completed_output_item() -> None:
    bridge = load_bridge_module()
    completed = {
        "item": {
            "type": "function_call",
            "status": "completed",
            "name": "ask_hermes",
            "call_id": "call-hermes",
            "arguments": '{"request":"turn on the light"}',
        }
    }
    incomplete = {"item": {**completed["item"], "status": "incomplete"}}

    assert bridge._completed_hermes_call("response.function_call_arguments.done", completed) is None
    assert bridge._completed_hermes_call("response.output_item.done", incomplete) is None
    assert bridge._completed_hermes_call("response.output_item.done", completed) == (
        "call-hermes",
        {"request": "turn on the light"},
    )
