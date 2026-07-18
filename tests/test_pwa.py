from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "reachy_mini_hermes" / "static"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_pwa_manifest_declares_standalone_root_scoped_app_and_icons() -> None:
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())

    assert manifest["name"] == "Reachy Mini Hermes"
    assert manifest["start_url"] == "/#dashboard"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    icons = {item["src"]: item for item in manifest["icons"]}
    assert icons["/static/icon-192.png"]["sizes"] == "192x192"
    assert icons["/static/icon-512.png"]["sizes"] == "512x512"
    assert icons["/static/icon-maskable-512.png"]["purpose"] == "maskable"
    assert _png_size(STATIC / "icon-192.png") == (192, 192)
    assert _png_size(STATIC / "icon-512.png") == (512, 512)
    assert _png_size(STATIC / "icon-maskable-512.png") == (512, 512)


def test_service_worker_caches_only_the_app_shell_and_bypasses_api() -> None:
    worker = (STATIC / "service-worker.js").read_text()

    assert 'url.pathname.startsWith("/api/")' in worker
    assert 'request.method !== "GET"' in worker
    assert 'caches.match("/")' in worker
    assert "/static/main.js" in worker
    assert "/static/style.css" in worker


def test_dashboard_exposes_native_install_prompt_with_http_fallback() -> None:
    html = (STATIC / "index.html").read_text()
    javascript = (STATIC / "main.js").read_text()
    backend = (ROOT / "reachy_mini_hermes" / "main.py").read_text()

    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'id="install-button"' in html
    assert "Add to Home screen" in html
    assert 'window.addEventListener("beforeinstallprompt"' in javascript
    assert 'navigator.serviceWorker.register("/service-worker.js"' in javascript
    assert 'window.isSecureContext' in javascript
    assert '@self.settings_app.get("/manifest.webmanifest"' in backend
    assert '@self.settings_app.get("/service-worker.js"' in backend
    assert '"Service-Worker-Allowed": "/"' in backend
