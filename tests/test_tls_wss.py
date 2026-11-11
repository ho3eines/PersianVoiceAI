"""``PVAI_SSL_CERTFILE`` must actually put the server behind TLS.

Two levels are checked, deliberately kept apart:

* **MEASURED here** — uvicorn is started as a real subprocess with a real
  self-signed certificate, and ``GET /health`` is read over ``https://``.  A
  plain-``http`` request to the same port must fail, otherwise "TLS is on"
  would just be a keyword in a settings object.
* **NOT MEASURED anywhere** — ESP32 firmware speaking ``wss://``, a real Caddy
  or nginx in front of the server, and the added latency of a proxy.  The
  guide (``docs/WSS_TLS.md``) says so explicitly; these tests only check that
  the guide documents the pieces a deployment needs.
"""

from __future__ import annotations

import http.client
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "WSS_TLS.md"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def self_signed_certificate(directory: Path) -> tuple[Path, Path]:
    """A throwaway certificate with 127.0.0.1 as SAN (7 days, no passphrase)."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed; cannot mint a test certificate")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "7",
            "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return cert, key


def wait_for_https(port: int, process: subprocess.Popen, timeout: float = 60.0):
    """Poll ``https://127.0.0.1:<port>/health`` until the server answers."""
    context = ssl._create_unverified_context()  # the certificate is self-signed
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=3, context=context)
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read()
            connection.close()
            return response.status, body
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            time.sleep(0.2)
    stderr = ""
    if process.stderr is not None:
        process.terminate()
        try:
            stderr = process.stderr.read().decode("utf-8", "replace")
        except (OSError, ValueError):  # pragma: no cover
            pass
    raise AssertionError(f"uvicorn did not serve https://127.0.0.1:{port}/health: {last}\n{stderr}")


def test_uvicorn_serves_https_when_the_certificate_is_configured(tmp_path: Path) -> None:
    cert, key = self_signed_certificate(tmp_path)
    port = free_port()
    environment = {
        **os.environ,
        "PVAI_HOST": "127.0.0.1",
        "PVAI_PORT": str(port),
        "PVAI_API_TOKEN": "",
        "PVAI_DATA_DIR": str(tmp_path / "data"),
        "PVAI_HISTORY_PERSIST": "false",
        "PVAI_WS_ENABLED": "false",
        "PVAI_SSL_CERTFILE": str(cert),
        "PVAI_SSL_KEYFILE": str(key),
        "PVAI_LOG_LEVEL": "warning",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "server"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        status, body = wait_for_https(port, process)
        assert status == 200
        assert b"persian-voice-ai" in body

        # The same port must *not* speak plain HTTP: that is what makes this a
        # TLS test rather than a "the server came up" test.
        plain = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        with pytest.raises((OSError, http.client.HTTPException, ssl.SSLError)):
            plain.request("GET", "/health")
            response = plain.getresponse()
            response.read()
            raise OSError(f"plain http answered with {response.status}")
        plain.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
            process.wait(timeout=15)


def test_uvicorn_stays_on_plain_http_without_a_certificate(tmp_path: Path) -> None:
    """Control case: the TLS path must not be what makes /health work."""
    port = free_port()
    environment = {
        **os.environ,
        "PVAI_HOST": "127.0.0.1",
        "PVAI_PORT": str(port),
        "PVAI_API_TOKEN": "",
        "PVAI_DATA_DIR": str(tmp_path / "data"),
        "PVAI_HISTORY_PERSIST": "false",
        "PVAI_WS_ENABLED": "false",
        "PVAI_SSL_CERTFILE": "",
        "PVAI_SSL_KEYFILE": "",
        "PVAI_LOG_LEVEL": "warning",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "server"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60.0
        while True:
            assert process.poll() is None, "server exited before answering"
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                connection.request("GET", "/health")
                response = connection.getresponse()
                body = response.read()
                connection.close()
                break
            except (OSError, http.client.HTTPException):
                if time.monotonic() > deadline:
                    raise AssertionError("plain HTTP server never answered")
                time.sleep(0.2)
        assert response.status == 200
        assert b"persian-voice-ai" in body
        # And TLS on that port must not work, since no certificate was given.
        context = ssl._create_unverified_context()
        with pytest.raises((OSError, ssl.SSLError, http.client.HTTPException)):
            connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=3, context=context)
            connection.request("GET", "/health")
            connection.getresponse().read()
            raise OSError("https answered although no certificate was configured")
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
            process.wait(timeout=15)


# ------------------------------------------------------------------ the guide
def test_the_wss_guide_exists_and_is_linked_from_the_protocol_document() -> None:
    assert GUIDE.is_file()
    protocol = (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    assert "WSS_TLS.md" in protocol


@pytest.mark.parametrize(
    "needle",
    [
        "wss://",
        "Caddy",
        "nginx",
        "proxy_pass",
        "Upgrade",
        "proxy_read_timeout",
        "PVAI_SSL_CERTFILE",
        "PVAI_WS_IDLE_TIMEOUT_S",
    ],
)
def test_the_guide_covers_the_pieces_a_deployment_needs(needle: str) -> None:
    assert needle in GUIDE.read_text(encoding="utf-8")


def test_the_guide_states_what_was_not_measured() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    section = re.search(r"اندازه‌گیری نشده[\s\S]*", text)
    assert section, "the guide must have an explicit NOT MEASURED section"
    assert section.group(0).count("NOT MEASURED") >= 3
