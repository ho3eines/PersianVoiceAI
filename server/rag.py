"""Small, fully local lexical RAG index; no embedding model or network calls."""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_CHUNK_CHARS = 900
_OVERLAP_CHARS = 120


def tokenize(text: str) -> list[str]:
    text = text.replace("ي", "ی").replace("ك", "ک").lower()
    return [word for word in _WORD.findall(text) if len(word) > 1]


@dataclass(frozen=True, slots=True)
class Chunk:
    document_id: str
    title: str
    text: str
    terms: Counter[str]


class KnowledgeBase:
    """JSON-backed local documents with deterministic BM25 chunk retrieval."""

    def __init__(self, root: str | Path, *, chunk_chars: int = _CHUNK_CHARS) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.chunk_chars = max(200, chunk_chars)
        self._lock = threading.RLock()

    def add(self, title: str, text: str) -> dict[str, str]:
        title, text = title.strip(), text.strip()
        if not title or not text:
            raise ValueError("عنوان و متن سند نباید خالی باشند.")
        document_id = uuid.uuid4().hex
        payload = {"id": document_id, "title": title[:200], "text": text[:200_000]}
        target = self.root / f"{document_id}.json"
        with self._lock:
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return {"id": document_id, "title": payload["title"]}

    def list(self) -> list[dict[str, str]]:
        with self._lock:
            result = []
            for path in sorted(self.root.glob("*.json")):
                try:
                    item = json.loads(path.read_text(encoding="utf-8"))
                    result.append({"id": str(item["id"]), "title": str(item["title"])})
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            return result

    def delete(self, document_id: str) -> bool:
        if not re.fullmatch(r"[0-9a-f]{32}", document_id):
            return False
        path = self.root / f"{document_id}.json"
        with self._lock:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

    def _chunks(self) -> list[Chunk]:
        chunks: list[Chunk] = []
        for item in self.list():
            path = self.root / f"{item['id']}.json"
            try:
                text = str(json.loads(path.read_text(encoding="utf-8"))["text"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            start = 0
            while start < len(text):
                end = min(len(text), start + self.chunk_chars)
                if end < len(text):
                    boundary = text.rfind("\n", start + self.chunk_chars // 2, end)
                    if boundary > start:
                        end = boundary
                section = text[start:end].strip()
                if section and tokenize(section):
                    chunks.append(Chunk(item["id"], item["title"], section, Counter(tokenize(section))))
                if end >= len(text):
                    break
                start = max(start + 1, end - _OVERLAP_CHARS)
        return chunks

    def search(self, query: str, *, limit: int = 4) -> list[dict[str, str | float]]:
        terms = tokenize(query)
        if not terms:
            return []
        chunks = self._chunks()
        if not chunks:
            return []
        average = sum(sum(chunk.terms.values()) for chunk in chunks) / len(chunks)
        document_frequency = Counter(term for chunk in chunks for term in chunk.terms)
        scored = []
        for chunk in chunks:
            length = sum(chunk.terms.values())
            score = 0.0
            for term in terms:
                frequency = chunk.terms.get(term, 0)
                if frequency:
                    inverse = math.log(1 + (len(chunks) - document_frequency[term] + 0.5) /
                                       (document_frequency[term] + 0.5))
                    score += inverse * frequency * 2.2 / (
                        frequency + 1.2 * (0.25 + 0.75 * length / max(average, 1))
                    )
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"id": chunk.document_id, "title": chunk.title, "text": chunk.text, "score": round(score, 5)}
            for score, chunk in scored[: max(1, min(limit, 10))]
        ]
