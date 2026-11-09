"""Text preparation for speech, and ASR hallucination control.

The defect these pin down: ``server/persian.py`` had ``normalize`` and
``spoken_numbers`` but nothing imported them, so the LLM's raw output —
Markdown, list numbers, emoji, digits — went straight to Piper.  Piper reads
what it is given: it says the asterisks, pronounces every list number with a
full stop, and skips an emoji with an audible hesitation.

The second half covers Whisper inventing sentences on silence.  A non-empty
transcript starts a whole LLM turn, so one hallucination is worse than several
misheard words: the assistant confidently answers a question nobody asked.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.live_voice import sentences
from server.persian import (
    false_accept_examples,
    false_accept_rate,
    integer_to_words,
    number_to_words,
    prepare_for_tts,
    split_tts_sentences,
)
from server.voice import LocalAudio, _is_hallucination

ZWNJ = "\u200c"
EZAFE = "\u0654"  # hamza above: makes "جملهٔ" read as "jomle-ye"


# --------------------------------------------------------- the reported cases
def test_the_reported_markdown_list_case() -> None:
    """Exactly the reproduction from the report.

    Before: ``['**مهم:** سه مورد:\\n1.', 'اول\\n2.', 'دوم']`` — the asterisks
    were read aloud and the list number became its own sentence.
    """
    raw = "**مهم:** سه مورد:\n1. اول\n2. دوم"
    assert prepare_for_tts(raw) == "مهم: سه مورد: اول دوم."
    # And the splitter no longer cuts on the numbers.
    assert sentences(raw, final=True)[0] == [raw]


def test_emoji_and_marks_are_not_pronounced() -> None:
    assert prepare_for_tts("عالی بود 😊 ممنون!") == "عالی بود ممنون!"
    assert "😊" not in prepare_for_tts("خوب 😊 بود")
    assert "🚀" not in prepare_for_tts("🚀 شروع")
    # A flag is two regional-indicator code points, not two letters.
    assert "🇮🇷" not in prepare_for_tts("ایران 🇮🇷")


def test_links_keep_their_label_and_drop_the_url() -> None:
    out = prepare_for_tts("برای دیدن [این لینک](https://example.com/x) کلیک کنید.")
    assert out == "برای دیدن این لینک کلیک کنید."
    assert "example.com" not in out
    assert "http" not in prepare_for_tts("به https://example.com برو")
    assert "wikipedia.org" not in prepare_for_tts("از wikipedia.org بخوان")


def test_code_blocks_are_dropped_inline_code_is_kept() -> None:
    """A fenced block is noise to a voice; an inline token is the answer."""
    assert prepare_for_tts("```python\nprint(1)\n```\nبعد ادامه.") == "بعد ادامه."
    assert prepare_for_tts("دستور `pip install x` است.") == "دستور pip install x است."


def test_list_markers_do_not_become_punctuation() -> None:
    assert prepare_for_tts("- یک\n- دو\n- سه") == "یک دو سه."
    assert prepare_for_tts("1) الف\n2) ب") == "الف ب."
    assert prepare_for_tts("* آیتم") == "آیتم."


def test_headings_quotes_and_rules_are_flattened() -> None:
    raw = "## عنوان\n> نقل قول\n---\nمتن *مورب* و **پررنگ** و ~~خط‌خورده~~."
    assert prepare_for_tts(raw) == "عنوان نقل قول متن مورب و پررنگ و خط‌خورده."


# ------------------------------------------------------------ numbers as words
@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "صفر"),
        (1, "یک"),
        (10, "ده"),
        (11, "یازده"),
        (19, "نوزده"),
        (20, "بیست"),
        (25, "بیست و پنج"),
        (100, "صد"),
        (123, "صد و بیست و سه"),
        (999, "نهصد و نود و نه"),
        (1000, "یک هزار"),
        (1234, "یک هزار و دویست و سی و چهار"),
        (1000000, "یک میلیون"),
        (1234567, "یک میلیون و دویست و سی و چهار هزار و پانصد و شصت و هفت"),
        (-5, "منفی پنج"),
    ],
)
def test_integers_are_spelled_in_persian(value: int, expected: str) -> None:
    assert integer_to_words(value) == expected


def test_a_scale_word_never_takes_a_conjunction() -> None:
    """"یک و میلیون" would be read as "one, and a million"."""
    assert "و میلیون" not in integer_to_words(1000000)
    assert integer_to_words(2000000) == "دو میلیون"
    # "صد میلیون", never "یک صد میلیون".
    assert integer_to_words(100000000) == "صد میلیون"
    assert integer_to_words(100000) == "صد هزار"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3.5", "سه ممیز پنج"),
        ("۳.۵", "سه ممیز پنج"),
        ("1,5", "یک ممیز پنج"),  # Persian convention: comma is the decimal
        ("1,234.5", "یک هزار و دویست و سی و چهار ممیز پنج"),
        ("1.234.567", "یک میلیون و دویست و سی و چهار هزار و پانصد و شصت و هفت"),
        ("1402", "یک هزار و چهارصد و دو"),
    ],
)
def test_number_to_words_handles_both_separator_conventions(
    raw: str, expected: str
) -> None:
    assert number_to_words(raw) == expected


def test_times_percentages_and_currency_are_spoken() -> None:
    assert prepare_for_tts("ساعت 10:30 است.") == "ده و سی دقیقه است."
    # Without the leading word the unit is supplied.
    assert prepare_for_tts("10:30 وقت داریم.") == "ساعت ده و سی دقیقه وقت داریم."
    assert prepare_for_tts("تخفیف ۲۵٪ شد.") == "تخفیف بیست و پنج درصد شد."
    assert prepare_for_tts("۲۵ درصد تخفیف") == "بیست و پنج درصد تخفیف."
    assert prepare_for_tts("قیمت ۳.۵ دلار است.") == "قیمت سه ممیز پنج دلار است."
    assert prepare_for_tts("۵ تومان دادم.") == "پنج تومان دادم."
    # Both currency conventions, so "$5" and "5$" read the same.
    assert prepare_for_tts("قیمت $5") == "قیمت پنج دلار."
    assert prepare_for_tts("قیمت 5$") == "قیمت پنج دلار."


def test_digits_from_every_script_become_words() -> None:
    """Persian, Arabic-Indic and ASCII digits must all be spoken."""
    for raw in ("7", "۷", "٧"):
        assert prepare_for_tts(f"{raw} کتاب") == "هفت کتاب."


# ------------------------------------------------------- Persian orthography
def test_the_ezafe_survives_preparation() -> None:
    """Stripping U+0654 turns "جملهٔ" into "جمله": a different word to a voice."""
    text = f"جمله{EZAFE} یک درست است."
    assert prepare_for_tts(text) == text


def test_arabic_letters_are_mapped_but_vocalisation_is_dropped() -> None:
    assert prepare_for_tts("كتاب علي") == "کتاب علی."
    assert prepare_for_tts("كِتَاب") == "کتاب."


def test_zwnj_is_preserved_for_the_voice() -> None:
    """"نیمفاصله" must stay; Piper uses it to avoid a glottal stop."""
    out = prepare_for_tts(f"کتاب{ZWNJ}های من")
    assert ZWNJ in out


def test_preparation_is_idempotent() -> None:
    """The pipeline can prepare a sentence that was already prepared."""
    samples = [
        "**مهم:** سه مورد:\n1. اول\n2. دوم",
        "ساعت 10:30 و ۲۵٪ و $5",
        "عالی بود 😊 ممنون!",
        "```\ncode\n```\nبعد",
    ]
    for raw in samples:
        once = prepare_for_tts(raw)
        assert prepare_for_tts(once) == once, raw


def test_empty_and_unpronounceable_input_yields_nothing() -> None:
    for raw in ("", "   ", "\n\n", "😊😊", "**", "```\nx\n```"):
        assert prepare_for_tts(raw) == "", raw


# ------------------------------------------------------------ a real corpus
#: Thirty Persian replies of the shape a local model actually produces: lists,
#: emphasis, links, numbers, times, prices, emoji, code, English terms.  Each
#: pair is (model output, what the voice must say instead).
SPEECH_CORPUS: list[tuple[str, str]] = [
    ("سلام! چطور می‌توانم کمک کنم؟", "سلام! چطور می‌توانم کمک کنم؟"),
    ("**توجه:** این کار برگشت‌پذیر است.", "توجه: این کار برگشت‌پذیر است."),
    ("1. برق را قطع کن\n2. پیچ را باز کن", "برق را قطع کن پیچ را باز کن."),
    ("- سیب\n- پرتقال\n- انار", "سیب پرتقال انار."),
    ("ساعت ۸:۱۵ جلسه داریم.", "هشت و پانزده دقیقه جلسه داریم."),
    ("قیمت ۱۲۹۰۰۰ تومان است.", "قیمت صد و بیست و نه هزار تومان است."),
    ("۳۰٪ تخفیف گرفتم.", "سی درصد تخفیف گرفتم."),
    ("فایل را از [اینجا](https://example.com/a.zip) بگیر.", "فایل را از اینجا بگیر."),
    ("عالی شد 🎉🎉", "عالی شد."),
    ("کد را با `git push` بفرست.", "کد را با git push بفرست."),
    ("## نتیجه\nهمه چیز درست است.", "نتیجه همه چیز درست است."),
    ("از `numpy` استفاده کن.", "از numpy استفاده کن."),
    ("> نقل قول از حافظ", "نقل قول از حافظ."),
    ("سال ۱۴۰۲ بود.", "سال یک هزار و چهارصد و دو بود."),
    ("عدد ۳.۵ را گرد کن.", "عدد سه ممیز پنج را گرد کن."),
    ("دمای بدن ۳۷.۲ بود.", "دمای بدن سی و هفت ممیز دو بود."),
    ("فاصله ۱۲ کیلومتر است.", "فاصله دوازده کیلومتر است."),
    ("۵ دستگاه وصل است.", "پنج دستگاه وصل است."),
    ("ساعت ۱۰:۰۰ شروع می‌شود.", "ده و صفر دقیقه شروع می‌شود."),
    # A version is read component by component, not as a decimal.
    ("نسخهٔ 2.1.0 منتشر شد.", "نسخهٔ دو نقطه یک نقطه صفر منتشر شد."),
    ("**مهم:** سه مورد:\n1. اول\n2. دوم\n3. سوم", "مهم: سه مورد: اول دوم سوم."),
    ("*italic* و **bold**", "italic و bold."),
    ("---\nپایان", "پایان."),
    ("[لینک خالی]()", "لینک خالی."),
    ("می‌خواهم بدانم چرا؟", "می‌خواهم بدانم چرا؟"),
    ("بله؛ اما نه.", "بله؛ اما نه."),
    ("عدد 1000000 بزرگ است.", "عدد یک میلیون بزرگ است."),
    ("۳ تا ۵ دقیقه طول می‌کشد.", "سه تا پنج دقیقه طول می‌کشد."),
    ("قیمت $۱۲ است.", "قیمت دوازده دلار است."),
    ("کتابم را بده.", "کتابم را بده."),
]


@pytest.mark.parametrize("raw,expected", SPEECH_CORPUS)
def test_corpus_of_real_shaped_replies_is_speakable(raw: str, expected: str) -> None:
    assert prepare_for_tts(raw) == expected


@pytest.mark.parametrize("raw,_expected", SPEECH_CORPUS)
def test_no_corpus_sample_keeps_markup_or_emoji(raw: str, _expected: str) -> None:
    out = prepare_for_tts(raw)
    for marker in ("**", "```", "`", "](http", "http://", "https://", "##", "_"):
        assert marker not in out, (marker, out)
    assert not any(
        0x1F000 <= ord(char) <= 0x1FAFF for char in out
    ), f"emoji left in {out!r}"


@pytest.mark.parametrize("raw,_expected", SPEECH_CORPUS)
def test_no_corpus_sample_keeps_raw_digits(raw: str, _expected: str) -> None:
    """Piper's Persian voices pronounce bare digits inconsistently."""
    out = prepare_for_tts(raw)
    assert not any(char.isdigit() for char in out), out


# --------------------------------------------------------- sentence splitting
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("یک. دو. سه.", ["یک.", "دو.", "سه."]),
        ("1. اول\n2. دوم", ["1. اول\n2. دوم"]),
        ("1) الف. 2) ب.", ["1) الف.", "2) ب."]),
        ("ساعت 10:30 است. بعد برو.", ["ساعت 10:30 است.", "بعد برو."]),
        ("عدد ۳.۵ است. بله.", ["عدد ۳.۵ است.", "بله."]),
        ("دکتر رفتم. خوب بود.", ["دکتر رفتم.", "خوب بود."]),
    ],
)
def test_sentences_never_split_a_list_number_or_a_decimal(
    raw: str, expected: list[str]
) -> None:
    assert sentences(raw, final=True)[0] == expected


def test_streaming_a_list_does_not_split_the_numbers() -> None:
    """The same text arriving in chunks must split like the whole."""
    text = "**مهم:** سه مورد:\n1. اول\n2. دوم"
    for step in (1, 2, 3, 5, 8):
        buffer: str = ""
        ready: list[str] = []
        for index in range(0, len(text), step):
            piece, buffer = sentences(buffer + text[index : index + step])
            ready.extend(piece)
        ready.extend(sentences(buffer, final=True)[0])
        assert "1." not in ready and "2." not in ready, (step, ready)


def test_split_tts_sentences_matches_the_offline_helper() -> None:
    text = "ساعت 10:30 است. عدد ۳.۵ خوب است؟"
    assert split_tts_sentences(text) == sentences(text, final=True)[0]


# --------------------------------------------------------- the pipeline wiring
def test_the_pipeline_speaks_prepared_text_not_raw_output() -> None:
    """The reported defect: *nothing* imported ``persian.py``.

    Grepping the source would pass on a comment, so this drives the real
    ``Pipeline`` and asserts what the speaker is actually handed.
    """
    import asyncio
    import threading

    from server.config import Settings
    from server.streaming_voice import Pipeline

    class Speaker:
        def __init__(self) -> None:
            self.spoken: list[str] = []

        def speak(self, text, token, *, on_first_chunk=None, on_first_frame=None,
                  on_complete=None):
            if not token.is_set():
                self.spoken.append(text)
            if on_complete is not None:
                on_complete(1.0, 100.0)

    class Audio:
        def transcribe_pcm(self, pcm: bytes) -> str:
            return "سلام"

    reply = "**مهم:** سه مورد:\n1. اول\n2. دوم با ۲۵٪ تخفیف 😊"
    body = (
        'data: {"choices":[{"delta":{"content":%s}}]}\n\n' % json.dumps(reply)
    ) + "data: [DONE]\n\n"

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            for line in body.splitlines():
                yield line

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *exc):
            return False

    class Client:
        async def aclose(self) -> None:
            return None

        def stream(self, *args, **kwargs):
            return Stream()

    speaker = Speaker()

    async def scenario() -> None:
        pipeline = Pipeline(
            Settings(history_persist=False),
            Audio(),
            speaker,
            Client(),
            lambda event: None,
        )
        pipeline.start()
        try:
            pipeline.conversation.add_user("سلام", utterance_id=1)
            turn = pipeline.start_reply(1)
            if pipeline.reply is not None:
                await asyncio.wait_for(pipeline.reply, timeout=10)
            # The play actor consumes the speech queue; give it a moment.
            for _ in range(200):
                if speaker.spoken:
                    break
                await asyncio.sleep(0.01)
            assert turn is not None
        finally:
            await pipeline.close()

    asyncio.run(scenario())
    spoken = " ".join(speaker.spoken)
    assert spoken, "the pipeline said nothing"
    # What reaches the voice is prepared: no markup, no emoji, digits as words.
    for marker in ("**", "1.", "2.", "😊", "%"):
        assert marker not in spoken, (marker, spoken)
    assert "بیست و پنج درصد" in spoken


def test_default_system_prompt_forbids_markup() -> None:
    """Cleaning up after the model is the fallback; not emitting it is better."""
    from server.config import DEFAULT_SYSTEM_PROMPT

    for word in ("markdown", "ایموجی", "فهرست", "حروف"):
        assert word in DEFAULT_SYSTEM_PROMPT, word
    assert len(DEFAULT_SYSTEM_PROMPT) < 2000, "a bloated prompt costs context"


# ---------------------------------------------------------- ASR hallucination
SILENCE_HALLUCINATIONS = [
    "زیرنویس‌ها توسط تیم ما تهیه شده است.",
    "ممنون از تماشای شما.",
    "از تماشای شما متشکرم",
    "لطفاً کانال ما را دنبال کنید.",
    "Subscribe to my channel",
    "ترجمهٔ فارسی:",
]


@pytest.mark.parametrize("text", SILENCE_HALLUCINATIONS)
def test_known_silence_artefacts_are_recognised(text: str) -> None:
    from server.config import Settings

    phrases = tuple(
        part.strip() for part in Settings().asr_hallucination_phrases.split(",")
    )
    assert _is_hallucination(text, phrases), text


@pytest.mark.parametrize(
    "text",
    [
        "سلام، حال شما چطور است؟",
        "چراغ را روشن کن",
        # "خداحافظ" alone is boilerplate, but inside a real sentence it is not.
        "به مادرم گفتم خداحافظ و رفتم",
    ],
)
def test_real_speech_is_not_a_hallucination(text: str) -> None:
    from server.config import Settings

    phrases = tuple(
        part.strip() for part in Settings().asr_hallucination_phrases.split(",")
    )
    assert not _is_hallucination(text, phrases), text


def test_the_phrase_filter_survives_arabic_letters_and_punctuation() -> None:
    """Normalisation must run first, or the filter misses the obvious case."""
    from server.config import Settings

    phrases = tuple(
        part.strip() for part in Settings().asr_hallucination_phrases.split(",")
    )
    assert _is_hallucination("«زیرنويس‌ها» ...", phrases)  # Arabic yeh
    assert _is_hallucination("  ممنون از تماشای شما!!  ", phrases)


def test_an_empty_phrase_list_disables_the_filter() -> None:
    assert not _is_hallucination("زیرنویس‌ها", ())


def _audio_with_segments(segments: list[SimpleNamespace], **overrides) -> LocalAudio:
    from server.config import Settings

    audio = LocalAudio.__new__(LocalAudio)
    audio.settings = Settings(**overrides)
    audio.metrics = SimpleNamespace(increment=lambda name: None)
    audio.model = SimpleNamespace(
        transcribe=lambda *args, **kwargs: (iter(segments), {"language": "fa"})
    )
    audio._lock = __import__("threading").Lock()
    audio._decode_lock = __import__("threading").Lock()
    return audio


def test_segments_with_high_no_speech_prob_are_dropped() -> None:
    """The model's own uncertainty is the strongest signal."""
    audio = _audio_with_segments(
        [
            SimpleNamespace(text="سکوت", no_speech_prob=0.95, avg_logprob=-0.2),
            SimpleNamespace(text="سلام", no_speech_prob=0.10, avg_logprob=-0.2),
        ]
    )
    assert audio._transcribe(None) == "سلام"


def test_segments_with_low_confidence_are_dropped() -> None:
    audio = _audio_with_segments(
        [
            SimpleNamespace(text="چیزی", no_speech_prob=0.1, avg_logprob=-2.5),
            SimpleNamespace(text="درست", no_speech_prob=0.1, avg_logprob=-0.3),
        ]
    )
    assert audio._transcribe(None) == "درست"


def test_repetition_loops_are_dropped() -> None:
    audio = _audio_with_segments(
        [
            SimpleNamespace(
                text="بله بله بله", no_speech_prob=0.1, avg_logprob=-0.2,
                compression_ratio=4.0,
            ),
        ]
    )
    assert audio._transcribe(None) == ""


def test_a_hallucinated_phrase_is_dropped_even_with_a_good_score() -> None:
    """The phrase list catches what the probabilities miss."""
    audio = _audio_with_segments(
        [SimpleNamespace(text="ممنون از تماشای شما", no_speech_prob=0.05, avg_logprob=-0.1)]
    )
    assert audio._transcribe(None) == ""


def test_silence_produces_an_empty_transcript_not_a_turn() -> None:
    """The whole point: nothing starts an LLM turn on silence."""
    audio = _audio_with_segments(
        [SimpleNamespace(text="زیرنویس‌ها", no_speech_prob=0.9, avg_logprob=-1.5)]
    )
    assert audio._transcribe(None) == ""


def test_transcribe_options_are_configurable() -> None:
    from server.config import Settings

    audio = LocalAudio.__new__(LocalAudio)
    audio.settings = Settings(
        asr_language="en",
        asr_initial_prompt="نام‌ها: تهران",
        asr_no_speech_threshold=0.4,
        asr_log_prob_threshold=-0.8,
    )
    options = audio._transcribe_options()
    assert options["language"] == "en"
    assert options["initial_prompt"] == "نام‌ها: تهران"
    assert options["no_speech_threshold"] == 0.4
    assert options["log_prob_threshold"] == -0.8
    # These two are not language-specific knobs; they stay fixed.
    assert options["condition_on_previous_text"] is False


def test_auto_language_is_allowed() -> None:
    from server.config import Settings

    audio = LocalAudio.__new__(LocalAudio)
    audio.settings = Settings(asr_language="auto")
    assert audio._transcribe_options()["language"] == "auto"


def test_an_empty_initial_prompt_is_not_sent() -> None:
    from server.config import Settings

    audio = LocalAudio.__new__(LocalAudio)
    audio.settings = Settings(asr_initial_prompt="  ")
    assert "initial_prompt" not in audio._transcribe_options()


# ---------------------------------------------------------- false-accept rate
def test_false_accept_rate_counts_only_real_transcripts() -> None:
    report = false_accept_rate(["", "   ", ".", "زیرنویس", "ممنون"])
    assert report["clips"] == 5
    assert report["accepted"] == 2
    assert report["false_accept_rate"] == 0.4


def test_false_accept_rate_of_an_empty_set_is_not_a_fake_zero() -> None:
    """``measured`` tells a reader the difference between 0 % and no data."""
    report = false_accept_rate([])
    assert report["measured"] == 0
    assert report["false_accept_rate"] == 0.0


def test_false_accept_examples_name_the_hallucination() -> None:
    examples = false_accept_examples(["", "زیرنویس", "  "])
    assert examples == ["زیرنویس"]


def test_the_benchmark_reports_false_accepts() -> None:
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "pvai_persian_eval_fa", root / "benchmarks" / "persian_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    data = module.load_set()
    reference = module.references(data)
    assert reference, "the speech set must still produce references"
    assert not any(key.startswith("ns-") for key in reference), (
        "negative-set clips must not be scored as WER references"
    )
    hypothesis = {key: "" for key in reference}
    hypothesis["ns-001"] = "زیرنویس‌ها توسط تیم ما تهیه شده است."
    report = module.score_no_speech(data, hypothesis)
    assert report["clips"] >= 1
    assert report["accepted"] == 1
    assert report["examples"] == ["زیرنویس‌ها توسط تیم ما تهیه شده است."]


def test_a_no_speech_entry_must_not_carry_a_reference() -> None:
    """A negative clip with text would silently re-enter the WER corpus."""
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "pvai_persian_eval_ns", root / "benchmarks" / "persian_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    data = {
        "normalization_policy": "x",
        "sentences": [
            {"id": "fa-1", "text": "سلام", "category": "greeting"},
            {"id": "ns-1", "kind": "no_speech", "text": "", "category": "no_speech"},
        ],
    }
    assert module.references(data) == {"fa-1": "سلام"}
    # And a speech entry with no text is still an error.
    with pytest.raises(ValueError):
        module.load_set(_write(tmp := Path("/tmp/_pvai_set.json"), {
            "sentences": [{"id": "fa-1", "text": "", "category": "x"}]
        }) or tmp)


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------- error messages
def test_missing_dependency_names_the_module_and_the_reason() -> None:
    """A generic "audio dependency missing" sent the user hunting."""
    from server.voice import VoiceError, dependency

    with pytest.raises(VoiceError) as caught:
        dependency("totally_missing_module_xyz")
    message = str(caught.value)
    assert "totally_missing_module_xyz" in message
    assert "No module named" in message
    assert "requirements-voice.txt" in message
    assert isinstance(caught.value.__cause__, ImportError)


# ------------------------------------------------------------------- robustness
#: Model output can be malformed in ways the regexes must not choke on: an
#: unclosed code fence, a link with no closing parenthesis, a wall of markers.
MALFORMED = [
    "[" * 500,
    "]" * 500,
    "*" * 200,
    "`" * 50,
    "(" * 100,
    "**بدون بسته شدن",
    "[لینک](بدون پرانتز بسته",
    "```بدون بستن بلوک",
    "\x00\x01\x02 control chars",
    "🙂" * 100,
    "متن" * 5000,
    "." * 100,
    "-" * 100,
]


@pytest.mark.parametrize("raw", MALFORMED)
def test_malformed_markup_never_raises(raw: str) -> None:
    """A crash here would kill the speak actor and stall the whole turn."""
    out = prepare_for_tts(raw)
    assert isinstance(out, str)


@pytest.mark.parametrize("raw", MALFORMED)
def test_malformed_markup_leaves_no_markup_behind(raw: str) -> None:
    out = prepare_for_tts(raw)
    for marker in ("**", "```", "*", "`", "[", "]", "http", "\x00"):
        assert marker not in out, (marker, out[:60])


def test_an_unclosed_link_still_speaks_its_label() -> None:
    """The label is the part a human hears; dropping it loses the sentence."""
    assert prepare_for_tts("[لینک](بدون پرانتز بسته") == "لینک."
    assert prepare_for_tts("[برچسب](a) و [ب](b") == "برچسب و ب."


def test_a_huge_integer_is_read_digit_by_digit() -> None:
    """Card numbers and tracking codes are identifiers, not quantities."""
    out = prepare_for_tts("12345678901234567890")
    assert not any(char.isdigit() for char in out)
    assert out.startswith("یک دو سه")


def test_control_characters_are_removed() -> None:
    assert prepare_for_tts("\x00\x01 متن \x7f") == "متن."
