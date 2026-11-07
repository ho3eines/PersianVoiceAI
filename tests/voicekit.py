"""Shared fakes for the conversation-engine tests (no model, no audio device)."""

from __future__ import annotations

import asyncio
import threading
from array import array
from typing import Any, Callable

from server.config import Settings

LOUD = 2000
FRAME_SAMPLES = 320


def frame(level: int = LOUD, samples: int = FRAME_SAMPLES) -> bytes:
    return array("h", [level] * samples).tobytes()


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


def feed_utterance(pipeline: Any, *, speech: int = 20, silence: int = 32,
                   level: int = LOUD) -> None:
    for _ in range(speech):
        pipeline.feed(frame(level))
    for _ in range(silence):
        pipeline.feed(frame(0))


def make_settings(**overrides: Any) -> Settings:
    """Settings with persistence disabled and no partial decodes by default."""
    base: dict[str, Any] = {
        "history_persist": False,
        "voice_asr_interval_s": 10.0,  # no partial decodes unless a test asks
    }
    base.update(overrides)
    return Settings(**base)


class FakeAsr:
    """Deterministic ASR with an optional gate to hold a decode in flight."""

    def __init__(self, texts: list[str] | None = None, text: str = "سلام دوست من") -> None:
        self.texts = list(texts) if texts is not None else None
        self.text = text
        self.calls: list[int] = []
        self.results: list[str] = []
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.gate.set()
        self.delay = 0.0

    def block(self) -> None:
        self.gate.clear()

    def release(self) -> None:
        self.gate.set()

    def transcribe_pcm(self, pcm: bytes) -> str:
        self.calls.append(len(pcm))
        self.entered.set()
        if not self.gate.is_set():
            self.gate.wait(5)
        if self.delay:
            import time

            time.sleep(self.delay)
        if self.texts:
            result = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        else:
            result = self.text
        self.results.append(result)
        return result


class FakeSpeaker:
    def __init__(self, on_speak: Callable[[str], None] | None = None) -> None:
        self.spoken: list[str] = []
        self.started: list[str] = []
        self.cancelled: list[bool] = []
        self.on_speak = on_speak

    def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None, on_complete=None):
        self.cancelled.append(token.is_set())
        self.started.append(text)
        if not token.is_set():
            self.spoken.append(text)
            if on_first_chunk is not None:
                on_first_chunk(1.0)
            if on_first_frame is not None:
                on_first_frame(1.01)
            if self.on_speak is not None:
                self.on_speak(text)
        if on_complete is not None:
            on_complete(1.02, 100.0)


def sse(*pieces: str, done: bool = True) -> str:
    body = "".join(
        'data: {"choices":[{"delta":{"content":"%s"}}]}\n\n' % piece for piece in pieces
    )
    return body + ("data: [DONE]\n\n" if done else "")


class _Response:
    def __init__(self, lines: Any) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    def aiter_lines(self):
        return self._lines


class _Context:
    def __init__(self, lines: Any, on_exit: Callable[[], None] | None) -> None:
        self._lines = lines
        self._on_exit = on_exit

    async def __aenter__(self) -> _Response:
        return _Response(self._lines)

    async def __aexit__(self, *args: Any) -> None:
        if self._on_exit is not None:
            self._on_exit()


class StreamClient:
    """Fake async HTTP client returning a scripted SSE stream."""

    def __init__(
        self,
        body: str = "",
        *,
        lines: Callable[[dict[str, Any]], Any] | None = None,
        on_exit: Callable[[], None] | None = None,
    ) -> None:
        self.body = body
        self._lines = lines
        self._on_exit = on_exit
        self.payloads: list[dict[str, Any]] = []
        self.opened = 0

    async def _aiter_body(self):
        for line in self.body.splitlines():
            if line.strip():
                yield line

    def stream(self, method: str, url: str, json: dict[str, Any] | None = None):
        self.payloads.append(json or {})
        self.opened += 1
        lines = self._lines(json or {}) if self._lines is not None else self._aiter_body()
        return _Context(lines, self._on_exit)
