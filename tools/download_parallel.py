"""Download a large model file over several parallel HTTP range requests.

Model files (GGUF weights, Whisper checkpoints) are multi-gigabyte. A single
connection stalls on a flaky link and cannot be resumed; this splits the file
into fixed-size parts, fetches several at once, and re-fetches only the parts
whose size does not match. Re-running after a failure resumes rather than
starting over.

    python tools/download_parallel.py --url URL --output models/model.gguf

Requires only the standard library, so it works in a bare Python install on
Windows, Linux and macOS alike.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB read buffer


def remote_length(url: str, retries: int = 3) -> int:
    """Total size from Content-Length, following redirects."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=30) as response:
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
            with urllib.request.urlopen(request, timeout=60) as response:
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


def download(url: str, output: Path, connections: int, part_mb: int, retries: int) -> Path:
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
    with output.open("wb") as handle:
        for path in sorted(expected):
            with path.open("rb") as part:
                while True:
                    block = part.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)

    written = output.stat().st_size
    if written != total:
        raise RuntimeError(f"assembled file is {written} bytes, expected {total}")
    for path in expected:
        path.unlink(missing_ok=True)
    work.rmdir()
    print(f"done: {output} ({written / (1 << 20):.1f} MB)")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="source URL (http/https)")
    parser.add_argument("--output", required=True, type=Path, help="destination file")
    parser.add_argument("--connections", type=int, default=10, help="parallel requests")
    parser.add_argument("--part-mb", type=int, default=64, help="size of each part in MiB")
    parser.add_argument("--retries", type=int, default=8, help="attempts per part")
    args = parser.parse_args(argv)

    if args.connections < 1:
        parser.error("--connections must be at least 1")
    if args.part_mb < 1:
        parser.error("--part-mb must be at least 1")

    try:
        download(args.url, args.output, args.connections, args.part_mb, args.retries)
    except (RuntimeError, urllib.error.URLError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
