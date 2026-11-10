"""Benchmark/evaluation tooling: honest reporting, no fabricated numbers.

The audit requires a full-pipeline benchmark and a Persian WER/CER evaluation.
Both tools must be runnable offline and must *label* what they measured:
``MEASURED`` / ``SIMULATED`` / ``NOT MEASURED``.  A tool that silently prints a
number it did not measure is worse than no tool at all.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def persian_eval():
    return load_module("pvai_persian_eval", "benchmarks/persian_eval.py")


@pytest.fixture(scope="module")
def benchmark_voice():
    return load_module("pvai_benchmark_voice", "tools/benchmark_voice.py")


# ------------------------------------------------------------------ eval set
def test_evaluation_set_is_valid_and_covers_persian_cases(persian_eval) -> None:
    data = persian_eval.load_set()
    assert len(data["sentences"]) >= 30
    categories = {entry["category"] for entry in data["sentences"]}
    # The hard Persian cases from the audit must be represented.
    for required in ("zwnj", "arabic_chars", "numbers", "datetime", "english_terms", "names"):
        assert required in categories
    for entry in data["sentences"]:
        if entry.get("kind") == "no_speech":
            # A negative-set clip: no reference text by definition, and it
            # must not pretend to be a scored Persian sentence.
            assert not entry["text"].strip()
            assert entry["id"].startswith("ns-")
            continue
        assert entry["text"].strip()
        assert entry["id"].startswith("fa-")
    # The silence/noise set exists, or the false-accept metric has no input.
    assert any(entry.get("kind") == "no_speech" for entry in data["sentences"])


def test_duplicate_ids_are_rejected(persian_eval, tmp_path: Path) -> None:
    payload = {
        "sentences": [
            {"id": "fa-1", "text": "یک", "category": "x"},
            {"id": "fa-1", "text": "دو", "category": "x"},
        ]
    }
    path = tmp_path / "set.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        persian_eval.load_set(path)


def test_perfect_transcript_scores_zero(persian_eval) -> None:
    data = persian_eval.load_set()
    reference = persian_eval.references(data)
    report = persian_eval.score(reference, dict(reference))
    assert report["wer"] == 0.0 and report["cer"] == 0.0
    assert report["sentences"] == len(reference)
    assert report["missing_ids"] == []


def test_formatting_only_differences_are_not_errors(persian_eval) -> None:
    """Arabic letters, digit style, ZWNJ-as-space and punctuation are policy.

    A model that writes ``ي`` and ``ك`` or Eastern digits is not making a
    recognition error; the reference is rewritten with those variants and the
    score must stay at zero.
    """
    data = persian_eval.load_set()
    reference = persian_eval.references(data)
    rewrite = (
        lambda text: text.replace("ی", "ي")
        .replace("ک", "ك")
        .replace("\u200c", " ")
        .translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        .replace("،", "")
        .replace("؟", "")
        .replace(".", "")
    )
    ids = ["fa-001", "fa-007", "fa-015", "fa-019"]
    hypothesis = {identifier: rewrite(reference[identifier]) for identifier in ids}
    report = persian_eval.score({identifier: reference[identifier] for identifier in ids}, hypothesis)
    assert report["wer"] == 0.0 and report["cer"] == 0.0


def test_missing_zwnj_is_reported_as_a_word_boundary_difference(persian_eval) -> None:
    """Documented limitation: dropping نیم‌فاصله *is* counted (one joined token).

    ``normalize_for_eval`` maps ZWNJ to a space; a model that outputs
    ``میروم`` instead of ``می‌روم`` therefore shows up as one word error.  This
    is intentional and visible in the ``zwnj`` category of the evaluation set —
    it must not be silently normalised away, because نیم‌فاصله is part of correct
    Persian orthography.
    """
    from server.persian import wer

    assert wer("می‌روم خانه", "میروم خانه") > 0
    assert wer("می‌روم خانه", "می روم خانه") == 0


def test_missing_ids_are_reported_not_silently_dropped(persian_eval) -> None:
    data = persian_eval.load_set()
    reference = persian_eval.references(data)
    hypothesis = {"fa-001": reference["fa-001"]}
    report = persian_eval.score(reference, hypothesis)
    assert report["sentences"] == 1
    assert len(report["missing_ids"]) == len(reference) - 1
    with pytest.raises(ValueError):
        persian_eval.score(reference, {"unknown-id": "متن"})


def test_cli_reports_not_measured_without_hypotheses(persian_eval, capsys) -> None:
    assert persian_eval.main([]) == 3
    assert "NOT MEASURED" in capsys.readouterr().out


def test_cli_scores_a_hypothesis_file(persian_eval, tmp_path: Path, capsys) -> None:
    data = persian_eval.load_set()
    reference = persian_eval.references(data)
    hypothesis = dict(reference)
    hypothesis["fa-001"] = "سلام حال شما چطور هست"  # one word substitution
    path = tmp_path / "hyp.json"
    path.write_text(json.dumps(hypothesis, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "report.json"
    code = persian_eval.main(["--hypotheses", str(path), "--json-out", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "WER" in text and "Persian ASR evaluation" in text
    report = json.loads(out.read_text(encoding="utf-8"))
    assert 0.0 < report["wer"] < 0.05
    assert report["by_category"]["greeting"]["wer"] > 0
    assert report["modes"] == {"hypotheses": True, "audio": False}


def test_cli_strict_fails_on_missing_ids(persian_eval, tmp_path: Path, capsys) -> None:
    data = persian_eval.load_set()
    first = data["sentences"][0]
    path = tmp_path / "partial.json"
    path.write_text(
        json.dumps({first["id"]: first["text"]}, ensure_ascii=False), encoding="utf-8"
    )
    assert persian_eval.main(["--hypotheses", str(path), "--strict"]) == 1
    capsys.readouterr()


# ------------------------------------------------------------------ benchmark
def test_benchmark_scenarios_are_declared(benchmark_voice) -> None:
    # The seven measurement groups the audit asks to report separately, plus
    # the historical "combined" alias.
    assert set(benchmark_voice.SCENARIOS) == {
        "idle",
        "asr",
        "llm",
        "tts",
        "asr_llm",
        "asr_llm_tts",
        "combined",
        "duplex",
        "bargein",
    }
    # Both labels exist; the default run measures the turn once, as asr_llm_tts.
    assert "combined" in benchmark_voice.SCENARIOS
    report = benchmark_voice.Report()
    report.add("idle", "x", 1.0, "ms", benchmark_voice.MEASURED)
    assert "MEASURED" in report.table()
    assert report.as_dict()["rows"][0]["metric"] == "x"


def test_status_labelling_is_conservative(benchmark_voice) -> None:
    worst = benchmark_voice._worst
    measured = benchmark_voice.MEASURED
    simulated = benchmark_voice.SIMULATED
    not_measured = benchmark_voice.NOT_MEASURED
    assert worst(measured, measured) == measured
    assert worst(measured, simulated) == simulated
    assert worst(simulated, not_measured) == not_measured


def test_benchmark_runs_idle_scenario_and_labels_every_row(benchmark_voice, tmp_path: Path, capsys) -> None:
    out = tmp_path / "bench.json"
    code = benchmark_voice.main(["--quick", "--scenarios", "idle", "--json-out", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "MEASURED" in printed and "NOT MEASURED" in printed  # legend
    payload = json.loads(out.read_text(encoding="utf-8"))
    metrics = {row["metric"] for row in payload["rows"]}
    assert {"audio_loop_jitter_p95_ms", "ram_usage_mb", "vad_false_positives"} <= metrics
    for row in payload["rows"]:
        assert row["status"] in {
            benchmark_voice.MEASURED,
            benchmark_voice.SIMULATED,
            benchmark_voice.NOT_MEASURED,
        }
    assert payload["environment"]["python"]


def test_benchmark_rejects_unknown_scenario(benchmark_voice, capsys) -> None:
    with pytest.raises(SystemExit):
        benchmark_voice.main(["--scenarios", "nope"])
    capsys.readouterr()


def test_silence_never_triggers_the_detector(benchmark_voice) -> None:
    """The idle scenario must not produce a false utterance on pure silence."""
    from server.config import Settings
    from server.metrics import Metrics

    metrics = Metrics()
    report = benchmark_voice.Report()
    settings = Settings(history_persist=False)
    benchmark_voice.scenario_idle_measured(report, settings, 0.2)
    rows = {row.metric: row for row in report.rows}
    assert rows["vad_false_positives"].value == 0


def test_text_benchmark_standalone_script_imports_local_auth_helper():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, 'benchmarks/bench_chat.py', '--help'], cwd=root,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


# ------------------------------------------------------- recording integrity
def test_read_wav_accepts_exactly_the_documented_format(persian_eval, tmp_path: Path) -> None:
    import warnings

    import numpy as np
    import soundfile as sf

    path = tmp_path / "fa-001.wav"
    sf.write(path, np.zeros(1600, dtype=np.float32), 16000, subtype="PCM_16")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # soundfile bit-depth chatter
        frames = persian_eval.read_wav_mono16k(path)
    assert len(frames) == 1600 * 2  # 16-bit mono


@pytest.mark.parametrize("kind", ["empty", "truncated", "not-a-wav", "stereo", "resampled"])
def test_unusable_recordings_name_the_file(persian_eval, tmp_path: Path, kind: str) -> None:
    """A broken recording must be a clear ``ValueError``, not a raw traceback.

    ``persian_eval --audio-dir`` used to die with ``wave.Error``/``EOFError``
    halfway through a scoring run, with nothing saying which of the forty
    recordings was the problem.  Stereo and non-16 kHz files are rejected on
    purpose: the documented policy is to refuse a format the pipeline does not
    produce (see ``docs/PC_VOICE.md``), never to silently resample it.
    """
    import numpy as np
    import soundfile as sf

    path = tmp_path / "fa-001.wav"
    if kind == "empty":
        sf.write(path, np.zeros(0, dtype=np.float32), 16000, subtype="PCM_16")
    elif kind == "truncated":
        sf.write(path, np.zeros(1600, dtype=np.float32), 16000, subtype="PCM_16")
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    elif kind == "not-a-wav":
        path.write_bytes(b"RIFF-not-really-a-wave-file")
    elif kind == "stereo":
        sf.write(path, np.zeros((1600, 2), dtype=np.float32), 16000, subtype="PCM_16")
    else:
        sf.write(path, np.zeros(4410, dtype=np.float32), 44100, subtype="PCM_16")

    with pytest.raises(ValueError) as excinfo:
        persian_eval.read_wav_mono16k(path)
    assert path.name in str(excinfo.value)


def test_audio_dir_scoring_closes_the_model(persian_eval, tmp_path: Path, monkeypatch) -> None:
    """The real Whisper model is released after a scoring run.

    Without this the tool left the model loaded, so an operator who reran it
    (or ran the benchmark afterwards) measured a machine that already held
    hundreds of megabytes of weights.
    """
    from types import SimpleNamespace

    import numpy as np
    import soundfile as sf

    import server.voice as voice_module

    closed: list[bool] = []

    class FakeAudio:
        def __init__(self, settings_obj=None, metrics=None) -> None:
            pass

        def validate(self) -> None:
            pass

        def transcribe_pcm(self, pcm, *args, **kwargs) -> str:
            return "سلام"

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(voice_module, "LocalAudio", FakeAudio)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    sf.write(audio_dir / "fa-001.wav", np.zeros(1600, dtype=np.float32), 16000, subtype="PCM_16")

    hypotheses, missing = persian_eval.transcribe_with_local_model(
        audio_dir, {"fa-001": "سلام"}, SimpleNamespace()
    )
    assert hypotheses == {"fa-001": "سلام"} and missing == []
    assert closed == [True]


def test_benchmark_closes_real_components(benchmark_voice) -> None:
    """``_close_quietly`` must release models and tolerate a missing ``close``."""
    released: list[str] = []

    class Component:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            released.append(self.name)

    benchmark_voice._close_quietly(Component("asr"))
    benchmark_voice._close_quietly(Component("tts"))
    benchmark_voice._close_quietly(object())  # no close method: not an error
    benchmark_voice._close_quietly(None)

    class Broken:
        def close(self) -> None:
            raise RuntimeError("device gone")

    benchmark_voice._close_quietly(Broken())  # cleanup never fails a run
    assert released == ["asr", "tts"]


def test_the_measured_policy_survives_real_asr_output_shapes(persian_eval) -> None:
    """End-to-end: transcripts the way a Persian ASR model actually writes them.

    Both differences below are *formatting* — Arabic ``ي``/``ك``/``إ`` instead of
    Persian letters, Persian thousands ``٬`` instead of ASCII digits — so the
    score must stay zero.  Before the review fixes the Arabic alef was left
    untouched and the thousands mark reached the comparison, so each of these
    produced a non-zero WER.
    """
    data = persian_eval.load_set()
    reference = persian_eval.references(data)
    hypothesis = dict(reference)
    hypothesis["fa-019"] = "مي‌خواهم يك فيلم فارسي ببينم."  # Arabic yeh + kaf
    hypothesis["fa-004"] = "شماره منزل من ٢٤ است و واحد سه."  # Arabic-Indic digits
    report = persian_eval.score(reference, hypothesis)
    assert report["wer"] == 0.0, report
    assert report["cer"] == 0.0, report


def test_policy_limits_are_documented_not_hidden(persian_eval) -> None:
    """The two known non-equivalences must keep costing, not be normalised away.

    * a quantity written as digits is not the same token as the same quantity
      written in words (``۱۲۰۰`` vs «هزار و دویست»);
    * the ezafe written as a نیم‌فاصله (``کتابخانهٔ`` → ``کتابخانه‌ی``) becomes
      two tokens, because the policy maps ZWNJ to a space.

    Both are stated in ``docs/BENCHMARKS.md``; pinning them stops a later
    "cleanup" from silently making the metric lenient.
    """
    from server.persian import wer

    assert wer("هزار و دویست تومان", "۱۲۰۰ تومان") > 0
    assert wer("کتابخانهٔ دانشگاه", "کتابخانه‌ی دانشگاه") > 0
