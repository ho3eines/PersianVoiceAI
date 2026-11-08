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
