"""Persian text normalization and ASR error metrics.

Persian ASR output differs from a human reference in ways that are *not*
recognition errors: Arabic ``ي``/``ك`` instead of Persian ``ی``/``ک``, Arabic
diacritics, ``ي`` vs ``ی``, Eastern vs Western digits, or the presence/absence of
نیم‌فاصله (U+200C).  Comparing raw strings therefore overstates the error rate
and makes it impossible to tell a real regression from a formatting difference.

The project needs a documented, reproducible policy before it may claim any
accuracy figure.  This module provides it:

* ``normalize`` — Unicode NFC, Arabic→Persian letter mapping, diacritic and
  tatweel removal, digit unification, optional ZWNJ handling, optional
  punctuation removal, whitespace collapse.
* ``normalize_for_eval`` — the policy used by WER/CER numbers reported in
  ``docs/PERFORMANCE.md`` (ZWNJ → space, punctuation removed, digits ASCII).
* ``wer`` / ``cer`` — Levenshtein-based Word/Character Error Rate.

No accuracy number is produced here by itself: without reference audio and real
Whisper weights the honest report is *NOT MEASURED*.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Sequence

ZWNJ = "\u200c"
TATWEEL = "\u0640"

# Arabic letter forms that Persian types differently.
LETTER_MAP = {
    "\u064a": "\u06cc",  # ARABIC YEH        ي → ی
    "\u0649": "\u06cc",  # ALEF MAKSURA      ى → ی
    "\u0643": "\u06a9",  # ARABIC KAF        ك → ک
    "\u06c0": "\u06c0",  # HEH WITH YEH (kept)
    "\u0629": "\u0647",  # TEH MARBUTA       ة → ه
}

DIGIT_MAP = {
    **{chr(0x06F0 + index): str(index) for index in range(10)},  # ۰-۹
    **{chr(0x0660 + index): str(index) for index in range(10)},  # ٠-٩
}

DIACRITICS = set(
    [chr(code) for code in range(0x064B, 0x0660)]
    + [chr(code) for code in range(0x0610, 0x061B)]
    + ["\u0670"]
)

PUNCTUATION = set(
    "،؛؟!.,:;?!…«»\"'()[]{}<>-–—_/\\|*&^%$#@~`+=ـ"
    "\u060c\u061b\u061f\u066b\u066c\u00ab\u00bb"
)

#: Characters that end a sentence in Persian, used by chunking and evaluation.
SENTENCE_PUNCTUATION = "؟?!.؛\n"

_ARABIC_QUESTION = "\u061f"


def normalize(
    text: str,
    *,
    zwnj: str = "keep",
    digits: str = "ascii",
    punctuation: bool = False,
    map_letters: bool = True,
    strip_diacritics: bool = True,
) -> str:
    """Normalize Persian text.

    ``zwnj`` — ``keep`` (default), ``space`` (split at نیم‌فاصله) or ``remove``.
    ``digits`` — ``ascii`` (default) or ``keep``.
    ``punctuation`` — ``True`` removes punctuation characters entirely.
    """
    if zwnj not in {"keep", "space", "remove"}:
        raise ValueError("zwnj must be 'keep', 'space' or 'remove'")
    if digits not in {"ascii", "keep"}:
        raise ValueError("digits must be 'ascii' or 'keep'")

    value = unicodedata.normalize("NFC", text or "")
    if strip_diacritics:
        value = "".join(char for char in value if char not in DIACRITICS)
    value = value.replace(TATWEEL, "")
    if map_letters:
        value = "".join(LETTER_MAP.get(char, char) for char in value)
    value = value.replace(_ARABIC_QUESTION, "؟")
    if digits == "ascii":
        value = "".join(DIGIT_MAP.get(char, char) for char in value)
    if zwnj == "space":
        value = value.replace(ZWNJ, " ")
    elif zwnj == "remove":
        value = value.replace(ZWNJ, "")
    if punctuation:
        value = "".join(char for char in value if char not in PUNCTUATION)
    return " ".join(value.split())


def normalize_for_eval(text: str) -> str:
    """The evaluation policy used for every WER/CER figure in this project."""
    return normalize(text, zwnj="space", digits="ascii", punctuation=True)


def tokens(text: str, *, for_eval: bool = True) -> list[str]:
    normalized = normalize_for_eval(text) if for_eval else normalize(text)
    return normalized.split()


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    """Levenshtein distance with a rolling row (O(min(n, m)) memory)."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, ref_item in enumerate(reference, start=1):
        current = [row]
        for column, hyp_item in enumerate(hypothesis, start=1):
            insert = current[column - 1] + 1
            delete = previous[column] + 1
            substitute = previous[column - 1] + (ref_item != hyp_item)
            current.append(min(insert, delete, substitute))
        previous = current
    return previous[-1]


def edit_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> dict[str, int]:
    """Substitutions, deletions and insertions of the minimal edit script.

    ``wer`` alone cannot tell a dropped syllable from an inserted word; a
    diagnosis needs the breakdown.  Memory is O(len(hypothesis)) per row plus a
    compact backtrace, which is fine for sentence-level evaluation.
    """
    rows, columns = len(reference) + 1, len(hypothesis) + 1
    distance = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        distance[row][0] = row
    for column in range(columns):
        distance[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            cost = 0 if reference[row - 1] == hypothesis[column - 1] else 1
            distance[row][column] = min(
                distance[row - 1][column] + 1,      # deletion
                distance[row][column - 1] + 1,      # insertion
                distance[row - 1][column - 1] + cost,
            )
    substitutions = deletions = insertions = 0
    row, column = len(reference), len(hypothesis)
    while row > 0 or column > 0:
        if row > 0 and column > 0:
            cost = 0 if reference[row - 1] == hypothesis[column - 1] else 1
            if distance[row][column] == distance[row - 1][column - 1] + cost:
                substitutions += cost
                row, column = row - 1, column - 1
                continue
        if row > 0 and distance[row][column] == distance[row - 1][column] + 1:
            deletions += 1
            row -= 1
            continue
        insertions += 1
        column -= 1
    return {
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
    }


def wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate after :func:`normalize_for_eval`; ``0.0`` for empty ref/hyp."""
    ref_tokens = tokens(reference)
    hyp_tokens = tokens(hypothesis)
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _edit_distance(ref_tokens, hyp_tokens) / len(ref_tokens)


def cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate after :func:`normalize_for_eval` (spaces kept)."""
    ref_chars = normalize_for_eval(reference)
    hyp_chars = normalize_for_eval(hypothesis)
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _edit_distance(list(ref_chars), list(hyp_chars)) / len(ref_chars)


def evaluate(pairs: Iterable[tuple[str, str]]) -> dict[str, float | int]:
    """Aggregate corpus-level WER/CER (total errors / total reference units)."""
    items = list(pairs)
    wer_errors = wer_units = cer_errors = cer_units = 0
    for reference, hypothesis in items:
        ref_tokens, hyp_tokens = tokens(reference), tokens(hypothesis)
        wer_errors += _edit_distance(ref_tokens, hyp_tokens)
        wer_units += len(ref_tokens)
        ref_chars = normalize_for_eval(reference)
        hyp_chars = normalize_for_eval(hypothesis)
        cer_errors += _edit_distance(list(ref_chars), list(hyp_chars))
        cer_units += len(ref_chars)
    return {
        "pairs": len(items),
        "wer": round(wer_errors / wer_units, 4) if wer_units else 0.0,
        "cer": round(cer_errors / cer_units, 4) if cer_units else 0.0,
        "word_units": wer_units,
        "char_units": cer_units,
    }


def evaluate_detailed(pairs: Iterable[tuple[str, str]]) -> dict[str, float | int]:
    """Corpus WER/CER plus the substitution/deletion/insertion breakdown."""
    items = list(pairs)
    totals = {
        "pairs": len(items),
        "substitutions": 0,
        "deletions": 0,
        "insertions": 0,
        "word_units": 0,
        "char_units": 0,
        "cer_errors": 0,
    }
    for reference, hypothesis in items:
        ref_tokens, hyp_tokens = tokens(reference), tokens(hypothesis)
        counts = edit_counts(ref_tokens, hyp_tokens)
        totals["substitutions"] += counts["substitutions"]
        totals["deletions"] += counts["deletions"]
        totals["insertions"] += counts["insertions"]
        totals["word_units"] += len(ref_tokens)
        ref_chars = normalize_for_eval(reference)
        hyp_chars = normalize_for_eval(hypothesis)
        totals["cer_errors"] += _edit_distance(list(ref_chars), list(hyp_chars))
        totals["char_units"] += len(ref_chars)
    errors = totals["substitutions"] + totals["deletions"] + totals["insertions"]
    return {
        "pairs": totals["pairs"],
        "wer": round(errors / totals["word_units"], 4) if totals["word_units"] else 0.0,
        "cer": round(totals["cer_errors"] / totals["char_units"], 4)
        if totals["char_units"]
        else 0.0,
        "substitutions": totals["substitutions"],
        "deletions": totals["deletions"],
        "insertions": totals["insertions"],
        "word_units": totals["word_units"],
        "char_units": totals["char_units"],
    }


def split_sentences(text: str) -> list[str]:
    """Split Persian text into sentences on terminal punctuation."""
    parts = re.split(rf"(?<=[{re.escape(SENTENCE_PUNCTUATION)}])\s+", (text or "").strip())
    return [part.strip() for part in parts if part.strip()]


def spoken_numbers(text: str) -> str:
    """Expand plain integers into Persian words (best-effort, digit by digit).

    Piper pronunciation of digits is inconsistent across Persian voices, so the
    TTS layer may prefer spelled-out numbers.  This helper intentionally keeps
    the mapping simple and predictable instead of inventing grammar.
    """
    digits = "صفر یک دو سه چهار پنج شش هفت هشت نه".split()
    out: list[str] = []
    for token in (text or "").split():
        if token.isdigit():
            out.append(" ".join(digits[int(char)] for char in token))
        else:
            out.append(token)
    return " ".join(out)
