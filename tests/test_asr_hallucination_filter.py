"""Whisper's hallucination filter: two agreeing scores, not one nervous guess.

On silence or noise Whisper does not return nothing — it returns a plausible
sentence.  Any non-empty transcript starts a full LLM turn, so the filter has to
be right in *both* directions:

* reject a segment only when ``no_speech_prob`` is high **and** ``avg_logprob``
  is low (the two scores agree the audio holds no reliable speech), or when
  ``compression_ratio`` shows the classic repetition loop, or when the text is
  one of the known silence phrases;
* never reject on a single score.  Quiet-but-clear Persian speech has a high
  ``no_speech_prob`` often enough; dropping it silently deletes the user's turn.

All thresholds come from ``Settings``, and nothing here measures accuracy: the
Persian numbers are **NOT MEASURED** (see ``docs/BENCHMARKS.md``) because there
is no labelled Persian silence/noise set with reference transcripts in the
repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.config import Settings
from server.voice import LocalAudio

ROOT = Path(__file__).resolve().parents[1]
SPEECH = "چراغ اتاق را روشن کن"


def segment(
    text: str = SPEECH,
    *,
    no_speech: float | None = 0.05,
    logprob: float | None = -0.2,
    ratio: float | None = 1.1,
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text, no_speech_prob=no_speech, avg_logprob=logprob, compression_ratio=ratio
    )


def audio(**settings: object) -> LocalAudio:
    return LocalAudio(Settings(**settings))


# --------------------------------------------------------------- the AND rule
def test_high_no_speech_with_a_good_logprob_is_kept() -> None:
    """One nervous score is not evidence: the words were clear."""
    filtered = audio(asr_max_no_speech_prob=0.5, asr_log_prob_threshold=-1.0)
    kept = segment(no_speech=0.95, logprob=-0.15)
    assert filtered._accepted_segments([kept]) == [kept]


def test_a_low_logprob_with_low_no_speech_is_kept() -> None:
    """A hard-to-decode but real utterance must survive."""
    filtered = audio(asr_max_no_speech_prob=0.75, asr_log_prob_threshold=-1.0)
    kept = segment(no_speech=0.1, logprob=-2.5)
    assert filtered._accepted_segments([kept]) == [kept]


def test_a_high_no_speech_prob_together_with_a_low_logprob_is_rejected() -> None:
    filtered = audio(asr_max_no_speech_prob=0.75, asr_log_prob_threshold=-1.0)
    assert filtered._accepted_segments([segment(no_speech=0.9, logprob=-1.4)]) == []


def test_missing_scores_never_reject_a_segment() -> None:
    """An ASR backend that reports no scores is not evidence of a hallucination."""
    filtered = audio()
    kept = segment(no_speech=None, logprob=None, ratio=None)
    assert filtered._accepted_segments([kept]) == [kept]


# ------------------------------------------------------- configurable numbers
def test_the_thresholds_are_read_from_configuration() -> None:
    strict = audio(asr_max_no_speech_prob=0.2, asr_log_prob_threshold=-0.3)
    loose = audio(asr_max_no_speech_prob=0.99, asr_log_prob_threshold=-4.0)
    borderline = segment(no_speech=0.5, logprob=-0.5)
    assert strict._accepted_segments([borderline]) == []
    assert loose._accepted_segments([borderline]) == [borderline]


def test_the_compression_ratio_threshold_is_configurable() -> None:
    strict = audio(asr_compression_ratio_threshold=1.5)
    loose = audio(asr_compression_ratio_threshold=4.0)
    looping = segment(text="بله بله بله بله", ratio=2.0)
    assert strict._accepted_segments([looping]) == []
    assert loose._accepted_segments([looping]) == [looping]


def test_a_compression_loop_is_rejected_on_its_own() -> None:
    filtered = audio(asr_compression_ratio_threshold=2.4)
    assert filtered._accepted_segments([segment(ratio=4.2)]) == []


# ------------------------------------------------------------ what docs claim
def _configuration_rows() -> dict[str, str]:
    text = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\|\s*`(PVAI_ASR_[A-Z0-9_]+)`\s*\|([^|]*)\|([^|]*)\|", line)
        if match:
            rows[match.group(1)] = match.group(3).strip()
    return rows


@pytest.mark.parametrize(
    "variable", ["PVAI_ASR_MAX_NO_SPEECH_PROB", "PVAI_ASR_LOG_PROB_THRESHOLD"]
)
def test_the_documented_rule_is_the_combination_not_a_single_score(variable: str) -> None:
    """The table used to promise a rejection on ``no_speech_prob`` alone."""
    row = _configuration_rows()[variable]
    assert "avg_logprob" in row and "no_speech_prob" in row, row
    assert "**کل سگمنت**" not in row, row


def test_benchmarks_declare_the_filter_accuracy_not_measured() -> None:
    """A threshold without a labelled Persian set has no measured accuracy."""
    text = (ROOT / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
    match = re.search(r"فیلتر توهم[\s\S]{0,2000}", text)
    assert match, "docs/BENCHMARKS.md has no section about the hallucination filter"
    assert "NOT MEASURED" in match.group(0)
