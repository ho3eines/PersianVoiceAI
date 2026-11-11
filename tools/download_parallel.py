"""Download a large model file over several parallel HTTP range requests.

Model files (GGUF weights, Whisper checkpoints) are multi-gigabyte. A single
connection stalls on a flaky link and cannot be resumed; this splits the file
into fixed-size parts, fetches several at once, and re-fetches only the parts
whose size does not match. Re-running after a failure resumes rather than
starting over.

    python tools/download_parallel.py --url HTTPS_URL --output models/model.gguf --sha256 EXPECTED_HEX

Requires only the standard library, so it works in a bare Python install on
Windows, Linux and macOS alike.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from urllib.parse import urlsplit
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB read buffer

KNOWN_MODEL_SUFFIXES = frozenset(
    {".gguf", ".onnx", ".bin", ".safetensors", ".pt", ".ckpt"}
)
"""Container formats this project loads as model weights.

A corrupt weight file does not fail loudly: inference returns nonsense, and the
nonsense is spoken out loud.  These are the extensions for which a checksum is
mandatory rather than optional.
"""

KNOWN_MODEL_HOSTS = frozenset(
    {"huggingface.co", "hf.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.huggingface.co"}
)
"""Hosts that serve this project's documented models (Qwen GGUF, Whisper,
Piper voices).  A file fetched from there is a model artefact whatever it is
called — ``config.json`` and ``tokenizer.json`` next to ``model.bin`` decide
whether a Whisper checkpoint is the one that was evaluated."""


def requires_checksum(url: str, output: Path) -> bool:
    """Is ``--sha256`` mandatory for this download?

    Known model artefacts are recognised by extension, by file name, and by
    *where they came from*: a Hugging Face ``resolve`` URL is a model file even
    when its name says ``tokenizer.json``.  There is deliberately no override
    flag — an unverified multi-gigabyte weight file is exactly the failure this
    tool exists to prevent.
    """
    name = output.name.lower()
    suffix = output.suffix.lower()
    if suffix in KNOWN_MODEL_SUFFIXES or name == "model.bin":
        return True
    host = (urlsplit(url).hostname or "").lower()
    return host in KNOWN_MODEL_HOSTS


def _safe_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return  # local test server / explicitly local source
    raise ValueError("https is required for remote model downloads")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        if urlsplit(request.full_url).scheme == "https" and urlsplit(newurl).scheme != "https":
            raise ValueError("https redirects must not downgrade to http")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open(request: urllib.request.Request, timeout: float):
    _safe_url(request.full_url)
    return urllib.request.build_opener(_SafeRedirect()).open(request, timeout=timeout)


def remote_length(url: str, retries: int = 3) -> int:
    """Total size from Content-Length, following redirects."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, method="HEAD")
            with _open(request, timeout=30) as response:
                length = response.headers.get("Content-Length")
                if length is None:
                    raise RuntimeError(f"no Content-Length for {url}")
                return int(length)
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"cannot read size of {url}: {last}")


def fetch_part(url: str, path: Path, start: int, length: int, retries: int) -> None:
    """Fetch one byte range, writing a temporary file then renaming it.

    The rename is what makes a part trustworthy: a part file that exists is
    always complete, so an interrupted run resumes cleanly.
    """
    expected_end = start + length - 1
    temporary = path.with_suffix(path.suffix + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"Range": f"bytes={start}-{expected_end}"}
            )
            with _open(request, timeout=60) as response:
                if response.status not in (200, 206):
                    raise RuntimeError(f"HTTP {response.status} for bytes {start}-{expected_end}")
                written = 0
                with temporary.open("wb") as handle:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        handle.write(block)
                        written += len(block)
            if written != length:
                raise RuntimeError(f"part {path.name}: got {written} of {length} bytes")
            temporary.replace(path)
            return
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            last = exc
            temporary.unlink(missing_ok=True)
            time.sleep(1 + attempt)
    raise RuntimeError(f"part {path.name} failed after {retries} attempts: {last}")


def download(url: str, output: Path, connections: int, part_mb: int, retries: int,
             sha256: str | None = None) -> Path:
    _safe_url(url)
    if not sha256 and requires_checksum(url, output):
        raise ValueError(
            "--sha256 is required for known model files "
            "(GGUF/ONNX/BIN/SafeTensors and anything from a model host); "
            "take the digest from the publisher's card or manifest"
        )
    if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("--sha256 must contain 64 hexadecimal characters")
    total = remote_length(url, retries=retries)
    part_size = part_mb << 20
    part_count = (total + part_size - 1) // part_size

    work = output.parent / (output.name + ".parts")
    work.mkdir(parents=True, exist_ok=True)
    print(
        f"total: {total / (1 << 20):.1f} MB | "
        f"parts: {part_count} (<= {part_mb} MB each) | concurrency: {connections}"
    )

    expected: dict[Path, int] = {}
    for index in range(part_count):
        start = index * part_size
        length = min(part_size, total - start)
        if length <= 0:
            break
        expected[work / f"part{index:04d}.bin"] = length

    for round_number in range(1, retries + 1):
        missing = {
            path: length
            for path, length in expected.items()
            if not path.exists() or path.stat().st_size != length
        }
        for path in expected:
            if path in missing:
                path.unlink(missing_ok=True)
        if not missing:
            break

        print(f"round {round_number}: downloading {len(missing)} missing part(s)")
        lock = threading.Lock()
        completed = 0

        def report(name: str) -> None:
            nonlocal completed
            with lock:
                completed += 1
                print(f"  [{completed}/{len(missing)}] {name}")

        with ThreadPoolExecutor(max_workers=max(1, connections)) as pool:
            futures = {}
            for path, length in missing.items():
                index = int(path.stem.removeprefix("part"))
                start = index * part_size
                future = pool.submit(fetch_part, url, path, start, length, retries)
                futures[future] = path.name
            for future in as_completed(futures):
                future.result()  # re-raise the first failure
                report(futures[future])
    # Check after the loop rather than relying on for/else, which fires even
    # when the final round fetched everything successfully.
    still_missing = [
        path for path, length in expected.items()
        if not path.exists() or path.stat().st_size != length
    ]
    if still_missing:
        raise RuntimeError(
            f"still missing {len(still_missing)} part(s) after {retries} round(s)"
        )

    print(f"joining {len(expected)} parts")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(output.name + ".part")
    with temporary_output.open("wb") as handle:
        for path in sorted(expected):
            with path.open("rb") as part:
                while True:
                    block = part.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)

    written = temporary_output.stat().st_size
    if written != total:
        temporary_output.unlink(missing_ok=True)
        raise RuntimeError(f"assembled file is {written} bytes, expected {total}")
    if sha256 is not None:
        digest = hashlib.sha256()
        with temporary_output.open("rb") as handle:
            for block in iter(lambda: handle.read(CHUNK), b""):
                digest.update(block)
        if digest.hexdigest() != sha256.lower():
            temporary_output.unlink(missing_ok=True)
            # A size-correct cached part may have been corrupted; discard all
            # cached parts so a retry cannot repeat the same bad assembly.
            for path in expected:
                path.unlink(missing_ok=True)
            raise RuntimeError("sha256 mismatch; existing destination was not replaced")
    temporary_output.replace(output)
    for path in expected:
        path.unlink(missing_ok=True)
    work.rmdir()
    print(f"done: {output} ({written / (1 << 20):.1f} MB)")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="source URL (https, or local http)")
    parser.add_argument("--output", required=True, type=Path, help="destination file")
    parser.add_argument("--sha256", help="trusted expected SHA-256 from model publisher")
    parser.add_argument("--connections", type=int, default=10, help="parallel requests")
    parser.add_argument("--part-mb", type=int, default=64, help="size of each part in MiB")
    parser.add_argument("--retries", type=int, default=8, help="attempts per part")
    args = parser.parse_args(argv)

    if args.connections < 1:
        parser.error("--connections must be at least 1")
    if args.part_mb < 1:
        parser.error("--part-mb must be at least 1")

    try:
        download(args.url, args.output, args.connections, args.part_mb, args.retries, args.sha256)
    except (RuntimeError, ValueError, urllib.error.URLError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
