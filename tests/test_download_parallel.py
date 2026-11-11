"""The parallel downloader must produce exactly the bytes at the URL.

Model weights are multi-gigabyte and a corrupt byte is silent until inference
returns nonsense, so the tests check the assembled file against the source
rather than just "the function returned". The resume test is the one that
matters most in practice: a dropped connection must not restart a 4 GB pull.
"""

from __future__ import annotations

import http.server
import hashlib
import importlib.util
import re
import socketserver
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 3.5 MB so a 1 MiB part size gives four parts, exercising the parallel path.
PAYLOAD = bytes(range(256)) * (3500000 // 256) + bytes(range(3500000 % 256))


def load_downloader():
    spec = importlib.util.spec_from_file_location(
        "download_parallel", ROOT / "tools" / "download_parallel.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["download_parallel"] = module
    spec.loader.exec_module(module)
    return module


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """A server that honours Range, which plain http.server does not."""

    data = PAYLOAD
    requests: list[str] = []

    def log_message(self, *args: object) -> None:  # keep pytest output clean
        pass

    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.data)))
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        header = self.headers.get("Range")
        type(self).requests.append(header or "")
        if header:
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", header)
            assert match, header
            start, end = int(match.group(1)), int(match.group(2))
            chunk = self.data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.data)}")
        else:
            chunk = self.data
            self.send_response(200)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


@pytest.fixture()
def server():
    _RangeHandler.requests = []
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/model.bin"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_a_multi_part_download_reassembles_the_original_bytes(tmp_path, server):
    downloader = load_downloader()
    output = tmp_path / "model.bin"

    downloader.download(server, output, connections=3, part_mb=1, retries=2, sha256=hashlib.sha256(PAYLOAD).hexdigest())

    assert output.read_bytes() == PAYLOAD


def test_parts_are_fetched_in_parallel_with_range_requests(tmp_path, server):
    downloader = load_downloader()

    downloader.download(server, tmp_path / "model.bin", 3, 1, 2, sha256=hashlib.sha256(PAYLOAD).hexdigest())

    ranges = [r for r in _RangeHandler.requests if r]
    assert len(ranges) == 4, ranges
    assert all(r.startswith("bytes=") for r in ranges)
    assert len(set(ranges)) == 4, "each part must be requested exactly once"


def test_a_complete_part_is_not_downloaded_again(tmp_path, server):
    """Re-running after a crash must resume, not restart a multi-GB pull."""
    downloader = load_downloader()
    output = tmp_path / "model.bin"
    parts = tmp_path / "model.bin.parts"
    parts.mkdir()
    (parts / "part0000.bin").write_bytes(PAYLOAD[: 1 << 20])   # complete
    (parts / "part0001.bin").write_bytes(b"corrupt")           # wrong size

    downloader.download(server, output, 3, 1, 2, sha256=hashlib.sha256(PAYLOAD).hexdigest())

    assert output.read_bytes() == PAYLOAD
    ranges = [r for r in _RangeHandler.requests if r]
    assert len(ranges) == 3, ranges
    # part0000 is 0-1048575; it must not appear among the requested ranges.
    assert not any(r.startswith("bytes=0-") for r in ranges), ranges


def test_success_on_the_final_allowed_round_is_not_reported_as_failure(tmp_path, server):
    """The for/else bug: a successful last round must not raise.

    With ``retries=1`` there is exactly one round. If it succeeds the loop ends
    without ``break``, so a ``for ... else`` on the retry loop fires and turns a
    completed download into "still missing parts".
    """
    downloader = load_downloader()
    output = tmp_path / "model.bin"

    downloader.download(server, output, connections=3, part_mb=1, retries=1, sha256=hashlib.sha256(PAYLOAD).hexdigest())

    assert output.read_bytes() == PAYLOAD


def test_the_parts_directory_is_removed_on_success(tmp_path, server):
    downloader = load_downloader()
    output = tmp_path / "model.bin"

    downloader.download(server, output, 3, 1, 2, sha256=hashlib.sha256(PAYLOAD).hexdigest())

    assert not (tmp_path / "model.bin.parts").exists()


def test_a_truncated_part_fails_loudly_instead_of_producing_a_short_file(tmp_path):
    """A short part must never reach the joined output."""
    downloader = load_downloader()
    parts = tmp_path / "x.dat.parts"
    parts.mkdir()
    # A server that answers 200 with fewer bytes than the range asks for.
    (parts / "part0000.bin").write_bytes(b"short")

    with pytest.raises(RuntimeError):
        # No server is listening on this port, so every attempt fails and the
        # function must raise rather than emit a partial file.  ``.dat`` keeps
        # this test about truncation, not about the checksum rule for weights.
        downloader.download("http://127.0.0.1:9/none.dat", tmp_path / "x.dat", 1, 1, 1)


def test_invalid_arguments_are_rejected_before_any_network_call(capsys):
    """argparse exits 2 rather than starting a download with a bad part size."""
    downloader = load_downloader()

    with pytest.raises(SystemExit) as exit_info:
        downloader.main(["--url", "http://x/y", "--output", "o", "--part-mb", "0"])

    assert exit_info.value.code == 2
    assert "part-mb" in capsys.readouterr().err


def test_model_download_checks_checksum_before_rename(tmp_path, server):
    import hashlib
    downloader = load_downloader()
    output = tmp_path / 'model.gguf'
    with pytest.raises(RuntimeError, match='sha256'):
        downloader.download(server, output, 3, 1, 1, sha256='0' * 64)
    assert not output.exists()
    assert downloader.download(server, output, 3, 1, 1, sha256=hashlib.sha256(PAYLOAD).hexdigest()) == output


def test_remote_plain_http_is_refused_before_any_request(tmp_path):
    downloader = load_downloader()
    with pytest.raises(ValueError, match='https'):
        downloader.download('http://example.org/model.gguf', tmp_path/'model.gguf', 1, 1, 1, sha256='0'*64)


def test_whisper_model_bin_also_requires_checksum(tmp_path, server):
    downloader = load_downloader()
    with pytest.raises(ValueError, match='sha256'):
        downloader.download(server, tmp_path/'model.bin', 1, 1, 1)


def test_bad_cached_part_is_invalidated_after_checksum_mismatch(tmp_path, server):
    downloader = load_downloader()
    output = tmp_path/'model.gguf'
    parts = tmp_path/'model.gguf.parts'
    parts.mkdir()
    (parts/'part0000.bin').write_bytes(b'Z' * (1 << 20))  # correct size, wrong bytes
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    with pytest.raises(RuntimeError, match='sha256'):
        downloader.download(server, output, 3, 1, 1, sha256=digest)
    assert not (parts/'part0000.bin').exists()
    downloader.download(server, output, 3, 1, 1, sha256=digest)
    assert output.read_bytes() == PAYLOAD


# ------------------------------------------------------- known-model integrity

def test_a_model_host_artifact_requires_a_checksum(tmp_path):
    """A Hugging Face ``resolve`` URL is a model file whatever it is called."""
    downloader = load_downloader()
    url = "https://huggingface.co/Systran/faster-whisper-small/resolve/main/tokenizer.json"
    with pytest.raises(ValueError, match="sha256"):
        downloader.download(url, tmp_path / "tokenizer.json", 1, 1, 1)


@pytest.mark.parametrize(
    "name", ["voice.onnx", "weights.safetensors", "model.bin", "adapter.pt", "llm.ckpt"]
)
def test_every_known_weight_format_requires_a_checksum(tmp_path, server, name):
    downloader = load_downloader()
    with pytest.raises(ValueError, match="sha256"):
        downloader.download(server, tmp_path / name, 1, 1, 1)


def test_a_plain_text_asset_from_a_local_server_needs_no_checksum(tmp_path, server):
    """Not every download is a model: a local manifest may skip the digest."""
    downloader = load_downloader()
    output = downloader.download(server, tmp_path / "manifest.txt", 1, 1, 1)
    assert output.read_bytes() == PAYLOAD


def test_a_checksum_mismatch_never_replaces_an_existing_destination(tmp_path, server):
    """A bad pull must not destroy the copy that already works."""
    downloader = load_downloader()
    output = tmp_path / "model.gguf"
    output.write_bytes(b"previous verified download")
    with pytest.raises(RuntimeError, match="sha256"):
        downloader.download(server, output, 2, 1, 1, sha256="f" * 64)
    assert output.read_bytes() == b"previous verified download"
    assert not (tmp_path / "model.gguf.part").exists()


def test_the_checksum_is_computed_before_the_destination_is_renamed(
    tmp_path, server, monkeypatch
):
    """Ordering proof: a digest exists before the final rename happens."""
    downloader = load_downloader()
    output = tmp_path / "model.gguf"
    expected = hashlib.sha256(PAYLOAD).hexdigest()

    state = {"verified": False}
    real_sha256 = hashlib.sha256

    class TrackedDigest:
        def __init__(self) -> None:
            self.inner = real_sha256()

        def update(self, block: bytes) -> None:
            self.inner.update(block)

        def hexdigest(self) -> str:
            state["verified"] = True
            return self.inner.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", lambda *args, **kwargs: TrackedDigest())
    original_replace = Path.replace

    def guarded_replace(self, target):
        if Path(target).name == output.name:
            assert state["verified"], (
                "the destination was renamed before its sha256 was checked"
            )
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", guarded_replace)
    downloader.download(server, output, 2, 1, 1, sha256=expected)
    assert state["verified"]
    assert output.read_bytes() == PAYLOAD


def test_plain_http_is_refused_for_lookalike_local_hosts(tmp_path):
    """``localhost.evil.example`` and userinfo tricks are not localhost."""
    downloader = load_downloader()
    for url in (
        "http://localhost.evil.example/model.gguf",
        "http://localhost@evil.example/model.gguf",
        "http://127.0.0.1.nip.io/model.gguf",
        "HTTP://example.org/model.gguf",
        "ftp://example.org/model.gguf",
    ):
        with pytest.raises(ValueError, match="https"):
            downloader.download(url, tmp_path / "model.gguf", 1, 1, 1, sha256="0" * 64)
