"""Persian normalization policy and WER/CER metrics (no fabricated accuracy)."""

from __future__ import annotations

from server.persian import (
    cer,
    evaluate,
    normalize,
    normalize_for_eval,
    split_sentences,
    spoken_numbers,
    tokens,
    wer,
)

ZWNJ = "\u200c"


def test_arabic_letters_are_mapped_to_persian() -> None:
    assert normalize("كتاب علي") == "کتاب علی"
    assert normalize("ى") == "ی"


def test_diacritics_and_tatweel_are_removed() -> None:
    assert normalize("كِتَاب") == "کتاب"
    assert normalize("ســـلام") == "سلام"


def test_digits_are_unified() -> None:
    assert normalize("۱۲۳ و ٤٥٦") == "123 و 456"
    assert normalize("۱۲۳", digits="keep") == "۱۲۳"


def test_zwnj_policies() -> None:
    assert normalize(f"می{ZWNJ}روم", zwnj="keep") == f"می{ZWNJ}روم"
    assert normalize(f"می{ZWNJ}روم", zwnj="space") == "می روم"
    assert normalize(f"می{ZWNJ}روم", zwnj="remove") == "میروم"


def test_punctuation_policy() -> None:
    assert normalize("سلام، حالت؟", punctuation=True) == "سلام حالت"
    assert normalize("سلام، حالت؟") == "سلام، حالت؟"


def test_normalize_for_eval_is_the_documented_policy() -> None:
    assert normalize_for_eval(f"می{ZWNJ}روم، ۱۲۳!") == "می روم 123"


def test_invalid_options_are_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        normalize("x", zwnj="collapse")
    with pytest.raises(ValueError):
        normalize("x", digits="roman")


def test_tokens_and_split_sentences() -> None:
    assert tokens("سلام، دوست من!") == ["سلام", "دوست", "من"]
    assert split_sentences("اول. دوم؟ سوم") == ["اول.", "دوم؟", "سوم"]


def test_word_and_character_error_rates() -> None:
    assert wer("سلام دوست من", "سلام دوست من") == 0.0
    assert wer("سلام دوست من", "سلام دوست") == 1 / 3
    assert wer("", "") == 0.0
    assert cer("کتاب", "کتب") == 0.25


def test_wer_ignores_formatting_only_differences() -> None:
    # Arabic yeh/kaf, digits and punctuation must not be counted as errors.
    assert wer("کتاب علي ۱۲۳", "كتاب علی 123!") == 0.0
    assert wer(f"می{ZWNJ}روم", "می روم") == 0.0


def test_corpus_evaluation_aggregates_errors() -> None:
    report = evaluate([("سلام دوست من", "سلام دوست"), ("حال شما", "حال شما")])
    assert report["pairs"] == 2
    assert report["word_units"] == 5
    assert report["wer"] == round(1 / 5, 4)
    assert 0 <= report["cer"] <= 1


def test_spoken_numbers_expands_digits() -> None:
    assert spoken_numbers("سال 1400") == "سال یک چهار صفر صفر"


# --------------------------------------------------------------- review fixes
# The four cases below were reproduced against the code before these fixes and
# each one silently corrupted either a WER figure or what the voice said.


def test_arabic_hamza_alefs_become_persian_alef() -> None:
    """``أ``/``إ``/``ٱ`` are the same phoneme as ``ا`` and were left Arabic.

    Before the fix ``normalize("إيران") == "إیران"``: the kaf/yeh half of the
    word was mapped and the alef was not, so the same spoken word cost one
    character error (and reached Piper as a letter the voice never saw).
    """
    assert normalize("أحمد") == "احمد"
    assert normalize("إيران") == "ایران"
    assert normalize("ٱيران") == "ایران"
    assert normalize("قرآن") == "قرآن"  # bare alef + hamza mark is preserved
    # A hamza on waw/yeh is a different letter in Persian and must stay.
    assert normalize("مؤمن") == "مؤمن"
    assert normalize("مسئول") == "مسئول"
    assert wer("إيران وأحمد رفتند", "ایران واحمد رفتند") == 0.0


def test_heh_with_yeh_above_is_the_ezafe_not_a_new_letter() -> None:
    """``ۀ`` (U+06C0) is ``ه`` + hamza; the identity mapping was a no-op."""
    assert normalize("خانۀ من") == normalize("خانهٔ من") == "خانه من"


def test_decimals_and_clock_times_keep_their_shape_for_wer() -> None:
    """Punctuation used to be *deleted*, gluing ``۳.۵`` into ``35``.

    A correct hypothesis scored as an error against the reference because the
    separator that carries the meaning was removed with the punctuation.
    """
    assert normalize_for_eval("۳٫۵ متر") == normalize_for_eval("3.5 متر")
    assert normalize_for_eval("ساعت ۳:۳۰") == normalize_for_eval("ساعت 3:30")
    assert normalize_for_eval("۱٬۲۳۴") == normalize_for_eval("1,234") == "1234"
    assert wer("۳٫۵ متر", "3.5 متر") == 0.0
    assert wer("ساعت ۳:۳۰", "ساعت 3:30") == 0.0
    # The failure this pins: a wrong number must still be a wrong number.
    assert wer("۳٫۵ متر", "35 متر") > 0


def test_persian_punctuation_becomes_a_space_not_a_deletion() -> None:
    """``کتاب، دفتر`` must not collapse into one token ``کتابدفتر``."""
    assert normalize_for_eval("کتاب، دفتر") == "کتاب دفتر"
    assert normalize_for_eval("کتاب دفتر") == "کتاب دفتر"
    assert wer("کتاب، دفتر", "کتاب دفتر") == 0.0


def test_percent_signs_are_the_same_symbol() -> None:
    assert normalize_for_eval("۲۵٪") == normalize_for_eval("۲۵%") == "25"
    assert normalize("۲۵٪") == "25%"


def test_invisible_paste_artefacts_are_removed_but_zwnj_is_not() -> None:
    """Bidi marks, ZWSP, ZWJ, soft hyphen and BOM carry no sound."""
    for hidden in ("\u200b", "\u200d", "\u00ad", "\ufeff", "\u200e", "\u2060"):
        assert normalize(f"می{hidden}روم") == "میروم", repr(hidden)
        assert normalize_for_eval(f"می{hidden}روم") == "میروم", repr(hidden)
    # The نیم‌فاصله is orthography, not noise: it survives the default policy.
    assert ZWNJ in normalize(f"می{ZWNJ}روم")


def test_a_stray_zwnj_never_reaches_a_token() -> None:
    """``ZWNJ.isspace()`` is False, so ``split()`` keeps it inside a token."""
    for raw in (f"می {ZWNJ}روم", f"{ZWNJ}می‌روم", f"می‌روم{ZWNJ}",
                f"می{ZWNJ}{ZWNJ}روم", f"می{ZWNJ} روم"):
        kept = normalize(raw)
        assert not kept.startswith(ZWNJ) and not kept.endswith(ZWNJ), raw
        assert f" {ZWNJ}" not in kept and f"{ZWNJ} " not in kept, raw
        assert ZWNJ * 2 not in kept, raw
        for token in tokens(raw, for_eval=False):
            assert ZWNJ not in token or token.strip(ZWNJ) == token, (raw, token)


def test_spoken_numbers_does_not_crash_on_superscripts() -> None:
    """``str.isdigit`` is True for ``²`` but ``int("²")`` raises ValueError."""
    assert spoken_numbers("عدد ۱۴۰۰ و ²") == "عدد یک چهار صفر صفر و ²"
