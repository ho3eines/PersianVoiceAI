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
import datetime
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


# --------------------------------------------------------------------------
# Text-to-speech preparation
#
# The LLM is asked for plain Persian prose, but a local model does not always
# comply and a user can also paste anything into the chat box.  Piper reads
# whatever it is given *literally*: it spells out ``**`` as noise, says
# "نقطه" after a list number, and skips an emoji with an audible hesitation.
# Worse, ``sentences()`` split on the dot of ``1.`` — the reproduction was
# ``"**مهم:** سه مورد:\n1. اول\n2. دوم"`` becoming
# ``['**مهم:** سه مورد:\n1.', 'اول\n2.', 'دوم']``, so every list number ended
# up as its own utterance.
#
# ``prepare_for_tts`` is the single entry point that turns model output into
# something a voice can read.  It is deliberately *destructive*: Markdown,
# code, links and URLs are removed rather than read aloud, and numbers are
# spelled out because Piper's Persian voices pronounce "10:30" inconsistently.
# --------------------------------------------------------------------------

#: Numbers are expanded up to this magnitude; larger ones are spelled digit by
#: digit (a year, a phone number and a price do not share a grammar).
SPELL_OUT_LIMIT = 1_000_000_000

_PERSIAN_DIGITS = "صفر یک دو سه چهار پنج شش هفت هشت نه".split()
_TEENS = "ده یازده دوازده سیزده چهارده پانزده شانزده هفده هجده نوزده".split()
_TENS = "بیست سی چهل پنجاه شصت هفتاد هشتاد نود".split()
_HUNDREDS = (
    "صد دویست سیصد چهارصد پانصد ششصد هفتصد هشتصد نهصد".split()
)
_SCALES = ("", "هزار", "میلیون", "میلیارد")

#: Currency words that should be read as a unit after the number.
_CURRENCY = {
    "تومان": "تومان",
    "ریال": "ریال",
    "دلار": "دلار",
    "یورو": "یورو",
    "پوند": "پوند",
    "$": "دلار",
    "€": "یورو",
    "£": "پوند",
    "﷼": "ریال",
}

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictographs, emoticons, transport, symbols
    "\U00002600-\U000027BF"  # misc symbols and dingbats
    "\U00002B00-\U00002BFF"  # arrows and shapes
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U00002190-\U000021FF"  # arrows
    "\U000020E3"             # combining enclosing keycap
    "]+"
)

_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_FENCE_RE = re.compile(r"```.*?(?:```|$)", re.S)
_MD_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_QUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.M)
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_BARE_DOMAIN_RE = re.compile(r"\b[\w.-]+\.(?:com|ir|org|net|io|dev|ai|co)\b\S*", re.I)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1", re.S)
_MD_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", re.M)
#: A link whose parentheses never closed.  The label is kept -- it is the part
#: a human hears ("[این لینک](https://…" must still say "این لینک").
#: "](…" with no closing ")" anywhere after it.
_UNBALANCED_LINK_RE = re.compile(r"(\[[^\]]*\])\([^)]*$")
#: Single emphasis markers left over after the balanced pass.
_UNBALANCED_MD_RE = re.compile(r"(?<!\w)[*_~]{1,3}(?!\w)")

#: ``1.`` / ``2)`` / ``-`` / ``•`` at the start of a line: a list marker, not a
#: sentence.  ``1.5`` (a decimal) must not match, hence the lookahead.
_LIST_MARKER_RE = re.compile(
    r"^[ \t]*(?:[0-9]{1,3}[.)](?=\s)|[•▪◦‣∙·*+\-–—](?=\s))\s*",
    re.M,
)

# ``ساعت`` is part of the match so "ساعت 10:30" does not become
# "ساعت ساعت ده و سی دقیقه", and a bare "10:30" still gets its unit word.
_TIME_RE = re.compile(
    r"(?:(?P<word>ساعت)\s*)?\b(?P<hour>[01]?\d|2[0-3])\s*[:٫]\s*(?P<minute>[0-5]\d)\b"
)
_PERCENT_RE = re.compile(r"(?P<number>[0-9][0-9.,]*)\s*(?:[٪%]|درصد)")
_CURRENCY_AFTER_RE = re.compile(
    r"(?P<number>[0-9][0-9.,]*)\s*(?P<unit>تومان|تومان‌ها|ریال|دلار|یورو|پوند|﷼)"
)
_CURRENCY_BEFORE_RE = re.compile(r"(?P<unit>[$€£])\s*(?P<number>[0-9][0-9.,]*)")
# ``5$`` is clockwise English, ``$5`` counter-clockwise; both are read aloud.
_CURRENCY_SUFFIX_RE = re.compile(r"(?P<number>[0-9][0-9.,]*)\s*(?P<unit>[$€£])")
# A number, optionally with a thousands group and a decimal part, in either the
# Persian or the ASCII convention.  Matched as a *whole* so ``1,234.5`` is read
# as one quantity instead of ``234.5`` plus a stranded ``1,``.
_NUMBER_RE = re.compile(
    r"[0-9]{1,3}(?:[.,][0-9]{3})+(?:[.,][0-9]+)?"
    r"|[0-9]+(?:[.,][0-9]+)?"
)
#: Brackets and stray glue that a voice would read as noise.
_NOISE_CHARS_RE = re.compile(r"[\[\]{}()<>|^~\\]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_DECIMAL_SEP = "٫"  # U+066B, the Persian decimal separator Piper voices know

#: Diacritics that carry meaning for a Persian voice and must survive:
#: U+0654 (hamza above) makes the ezafe audible — "جملهٔ" is "jomle-ye", and
#: stripping it turns the word into a bare "جمله".  Vocalisation marks
#: (fatha/kasra/damma/tanwin/sukun) are absent from normal text and are
#: removed, because Piper reads them as separate phonemes.
_TTS_KEEP_DIACRITICS = {"\u0653", "\u0654", "\u0655"}


def _strip_tts_diacritics(value: str) -> str:
    return "".join(
        char
        for char in value
        if char not in DIACRITICS or char in _TTS_KEEP_DIACRITICS
    )


def _three_digits(value: int, *, after_scale: bool = False) -> list[str]:
    """Persian words for 1..999.

    ``after_scale`` drops the "یک" of a leading hundred: ``100000000`` is
    "صد میلیون", not "یک صد میلیون".
    """
    words: list[str] = []
    hundreds, rest = divmod(value, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds - 1])
    if rest >= 20:
        tens, unit = divmod(rest, 10)
        words.append(_TENS[tens - 2])
        if unit:
            words.append(_PERSIAN_DIGITS[unit])
    elif rest >= 10:
        words.append(_TEENS[rest - 10])
    elif rest:
        words.append(_PERSIAN_DIGITS[rest])
    return words


def integer_to_words(value: int) -> str:
    """Spell a whole number in Persian (``0`` included).

    ``1234`` → ``"یک هزار و دویست و سی و چهار"``.  Grouped in threes up to
    ``SPELL_OUT_LIMIT``; beyond that the digits are read one by one, because a
    number that large is an identifier (a card, a tracking code), not a
    quantity, and there is no natural reading of it.
    """
    if value < 0:
        return "منفی " + integer_to_words(-value)
    if value == 0:
        return "صفر"
    if value >= SPELL_OUT_LIMIT:
        # An identifier, not a quantity: read it digit by digit rather than
        # leaving ASCII digits for Piper to pronounce inconsistently.
        return " ".join(_PERSIAN_DIGITS[int(char)] for char in str(value))

    groups: list[str] = []
    scale = 0
    remainder = value
    while remainder:
        remainder, chunk = divmod(remainder, 1000)
        if chunk:
            # "یک میلیون", never "یک و میلیون": the scale word follows the
            # group without a conjunction, and only the groups are joined by و.
            part = " و ".join(_three_digits(chunk))
            if scale:
                # "صد میلیون", not "یک صد میلیون"; "یک هزار" stays as it is
                # because "هزار" on its own is not idiomatic Persian either.
                if part == "یک صد":
                    part = "صد"
                part = f"{part} {_SCALES[scale]}"
            groups.append(part)
        scale += 1
    return " و ".join(reversed(groups))


def number_to_words(text: str) -> str:
    """Spell a numeric token, keeping a decimal part.

    Accepts ASCII digits plus the Persian/Arabic forms (normalised first).
    ``"3.5"`` → ``"سه ممیز پنج"``; ``"1402"`` → ``"یک هزار و چهارصد و دو"``.
    A ``.`` that groups thousands (``1.234.567``) is *not* a decimal point.
    """
    value = _to_ascii_digits((text or "").strip())
    if not value:
        return ""
    value = _normalise_separators(value)
    whole, dot, fraction = value.partition(".")
    try:
        spoken = integer_to_words(int(whole or "0"))
    except ValueError:
        return value
    if dot and fraction:
        spoken += " ممیز " + integer_to_words(int(fraction))
    return spoken


def _normalise_separators(value: str) -> str:
    """Decide which separators are thousands groups and which are decimals.

    Persian writes decimals with ``٫`` (U+066B) and thousands with ``,``.  Text
    pasted from elsewhere mixes conventions, so: a ``.`` or ``,`` followed by
    exactly three digits and then a separator is a group; anything else is a
    decimal point.  ``1,234.5`` → ``1234.5``; ``1,5`` → ``1.5``;
    ``1.234.567`` → ``1234567``.
    """
    value = value.replace(_DECIMAL_SEP, ".").strip()
    groups = re.fullmatch(
        r"([0-9]{1,3}(?:[.,][0-9]{3})+(?:[.,][0-9]+)?)|([0-9]+[.,][0-9]+)|([0-9]+)",
        value,
    )
    if groups is None:
        return value
    text = value
    # Strip every separator that is followed by exactly three digits and is not
    # the *last* separator in a decimal number.
    thinned = re.sub(r"(?<=\d)[.,](?=\d{3}(?:[.,]|$))", "", text)
    # Whatever single separator remains is the decimal point.
    thinned = thinned.replace(",", ".")
    if thinned.count(".") > 1:
        parts = thinned.split(".")
        thinned = "".join(parts[:-1]) + "." + parts[-1]
    return thinned


def _to_ascii_digits(text: str) -> str:
    """Persian (۰-۹) and Arabic-Indic (٠-٩) digits → ASCII."""
    return "".join(DIGIT_MAP.get(char, char) for char in (text or ""))


def split_tts_sentences(text: str) -> list[str]:
    """Split text into utterances without breaking on list numbers or decimals.

    ``sentences()`` in :mod:`server.live_voice` splits on any ``.`` followed by
    whitespace, which turns ``"1. اول"`` into two utterances and ``"۳.۵"`` into
    two as well.  This splitter protects both, and also keeps a trailing
    ``.`` attached to the sentence it ends.
    """
    value = (text or "").strip()
    if not value:
        return []
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\u0000{len(protected) - 1}\u0000"

    # Hide decimals and clock times from the sentence splitter.
    value = re.sub(r"\b[0-9]+\.[0-9]+\b", _protect, value)
    value = _TIME_RE.sub(_protect, value)

    parts = re.split(
        r"(?<=[.!؟?؛])\s+|\n+",
        value,
    )
    result: list[str] = []
    for part in parts:
        restored = re.sub(
            r"\u0000([0-9]+)\u0000", lambda m: protected[int(m.group(1))], part
        ).strip()
        if restored:
            result.append(restored)
    return result


def prepare_for_tts(text: str) -> str:
    """Turn model output into text a Persian voice can read aloud.

    Removes Markdown, code, links, URLs, HTML and emoji; flattens list markers;
    then spells out times, percentages, currency amounts and plain numbers.
    Punctuation is kept (Piper uses it for prosody) but the نیم‌فاصله is
    normalised so a voice does not insert a glottal stop mid-word.

    Idempotent: running it twice produces the same string, which matters
    because the pipeline may prepare a sentence that was already prepared
    incrementally.
    """
    value = unicodedata.normalize("NFC", text or "")

    # --- structure that must disappear entirely -------------------------
    value = _MD_FENCE_RE.sub(" ", value)          # fenced code blocks
    value = _MD_IMAGE_RE.sub(" ", value)          # images (alt text is not speech)
    # A never-closed link first: the balanced pattern below would treat its
    # bracket as plain text and the label would disappear with it.
    value = _UNBALANCED_LINK_RE.sub(
        lambda m: m.group(1)[1:-1], value
    )
    value = _MD_LINK_RE.sub(r"\1", value)         # keep the label, drop the URL
    value = _URL_RE.sub(" ", value)
    value = _BARE_DOMAIN_RE.sub(" ", value)
    value = _HTML_TAG_RE.sub(" ", value)
    value = _MD_RULE_RE.sub(" ", value)
    value = _MD_INLINE_CODE_RE.sub(r"\1", value)  # keep the code, it is read

    value = _MD_HEADING_RE.sub(" ", value)        # "# title" → "title"
    value = _MD_QUOTE_RE.sub("", value)           # "> quote" → "quote"
    value = _LIST_MARKER_RE.sub("", value)        # "1. ", "- " → ""
    value = _MD_EMPHASIS_RE.sub(r"\2", value)     # **bold** / _italic_ / ~~strike~~
    # Unbalanced leftovers ("**بدون بسته شدن"), which the pattern above cannot
    # match.  A voice reads every asterisk, so no marker may survive.
    value = value.replace("**", " ").replace("~~", " ").replace("__", " ")
    value = _UNBALANCED_MD_RE.sub(" ", value)
    # A link whose parentheses never closed keeps its bracket syntax otherwise.
    value = _UNBALANCED_LINK_RE.sub(" ", value)
    value = _EMOJI_RE.sub(" ", value)
    value = value.replace("*", " ")               # stray asterisks are noise
    value = value.replace("`", "")

    # --- letters and spacing -------------------------------------------
    # ``normalize`` is the *evaluation* policy and strips every diacritic; a
    # voice needs the ezafe, so the hamza forms are restored afterwards.
    value = _strip_tts_diacritics(unicodedata.normalize("NFC", value))
    value = "".join(LETTER_MAP.get(char, char) for char in value)
    value = value.replace(TATWEEL, "").replace(_ARABIC_QUESTION, "؟")
    value = _to_ascii_digits(value)
    value = _CONTROL_RE.sub(" ", value)
    value = _NOISE_CHARS_RE.sub(" ", value)
    value = value.replace(ZWNJ, "\u200c")  # keep an explicit, single form
    value = re.sub(r"[ \t]+", " ", value)
    value = "\n".join(line.strip() for line in value.splitlines())
    value = re.sub(r"\n{2,}", "\n", value).strip()
    if not value:
        return ""
    # --- numbers as words ----------------------------------------------
    spoken = _speak_numbers(value)
    # The period goes on *after* expansion: ``"قیمت $5"`` ends with a digit at
    # this point and would otherwise come back without its full stop.
    if spoken and not spoken.rstrip().endswith(tuple(".!؟?؛")):
        spoken = spoken.rstrip() + "."
    return spoken


_VERSION_RE = re.compile(r"\b[0-9]+(?:\.[0-9]+){2,}\b")


def _speak_numbers(value: str) -> str:
    """Replace times, percentages, currency and bare numbers with words."""

    def _time(match: re.Match[str]) -> str:
        hour, minute = int(match.group("hour")), int(match.group("minute"))
        spoken = f"{integer_to_words(hour)} و {integer_to_words(minute)} دقیقه"
        # Only add the unit word when the text does not already say it.
        prefix = "" if match.group("word") else "ساعت "
        return prefix + spoken

    def _percent(match: re.Match[str]) -> str:
        return f"{number_to_words(match.group('number'))} درصد"

    def _currency_after(match: re.Match[str]) -> str:
        unit = match.group("unit")
        return f"{number_to_words(match.group('number'))} {_CURRENCY.get(unit, unit)}"

    def _currency_before(match: re.Match[str]) -> str:
        return (
            f"{number_to_words(match.group('number'))} "
            f"{_CURRENCY[match.group('unit')]}"
        )

    # ``2.1.0`` is a version, not a decimal followed by a sentence: read every
    # component separately.  Done first, or the generic number rule splits it
    # into "دو ممیز یک" plus a stranded ".صفر".
    def _version(match: re.Match[str]) -> str:
        return " نقطه ".join(
            number_to_words(part) for part in match.group(0).split(".")
        )

    # Protect Latin model names: GPT-4o and Qwen3 are identifiers, not
    # quantities. A dotted version is different: say each component.
    protected: list[str] = []
    def protect(text: str) -> str:
        protected.append(text)
        return chr(0xE000 + len(protected) - 1)

    def _latin(match: re.Match[str]) -> str:
        text = match.group(0)
        version = re.fullmatch(r"[vV]([0-9]+(?:\.[0-9]+){2,})", text)
        if version:
            return protect("نسخه " + _version_version(version.group(1)))
        return protect(text)

    def _version_version(text: str) -> str:
        return " نقطه ".join(number_to_words(part) for part in text.split("."))

    value = re.sub(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*", _latin, value)

    # The slash means date or fraction, not a mathematical decimal. Avoid
    # reinterpreting an invalid date as a valid one.
    months = ("", "ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر")
    def _date(match: re.Match[str]) -> str:
        year, month, day = map(int, match.group(0).split("/"))
        try:
            datetime.date(year, month, day)
        except ValueError:
            return match.group(0)
        return f"{integer_to_words(day)} {months[month]} {integer_to_words(year)}"

    value = re.sub(r"(?<!\d)[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}(?!\d)", _date, value)
    value = re.sub(r"(?<!\d)0[0-9]{3}-[0-9]{3}-[0-9]{4}(?!\d)",
                   lambda m: " ".join(integer_to_words(int(d)) for d in m.group(0) if d.isdigit()), value)
    fractions = {2: "دوم", 3: "سوم", 4: "چهارم", 5: "پنجم", 6: "ششم", 7: "هفتم", 8: "هشتم", 9: "نهم", 10: "دهم"}
    value = re.sub(r"(?<![\d/])([0-9]+)/([2-9]|10)(?![\d/])",
                   lambda m: f"{integer_to_words(int(m[1]))} {fractions[int(m[2])]}", value)
    # A leading minus is a sign, not punctuation; list markers have already
    # been removed earlier in prepare_for_tts.
    value = re.sub(r"(?<![\w\d])-([0-9])", r"منفی \1", value)
    value = _VERSION_RE.sub(_version, value)
    value = _TIME_RE.sub(_time, value)
    value = _PERCENT_RE.sub(_percent, value)
    value = _CURRENCY_AFTER_RE.sub(_currency_after, value)
    value = _CURRENCY_SUFFIX_RE.sub(_currency_before, value)
    value = _CURRENCY_BEFORE_RE.sub(_currency_before, value)
    value = _NUMBER_RE.sub(lambda m: number_to_words(m.group(0)), value)
    value = value.replace(_DECIMAL_SEP, " ممیز ")
    value = " ".join(value.split())
    for index, text in enumerate(protected):
        value = value.replace(chr(0xE000 + index), text)
    return value


# --------------------------------------------------------------------------
# False-accept rate (silence / noise)
#
# WER and CER only describe what happens when there *was* speech.  They cannot
# see the failure that matters most in a voice assistant: Whisper inventing a
# sentence on silence.  Because a non-empty transcript starts a full LLM turn,
# one hallucination is worse than a dozen misheard words — the assistant
# confidently answers a question nobody asked, spends TTS time and interrupts
# the music the microphone picked up.
#
# A false accept needs a *negative* set: clips where the reference is empty
# (silence, room noise, TV in the background).  The rate below is the share of
# those clips that produced any text at all.
# --------------------------------------------------------------------------


def false_accept_rate(hypotheses: Iterable[str]) -> dict[str, float | int]:
    """Share of no-speech clips that produced a transcript anyway.

    Each input is the hypothesis for a clip whose reference is *empty*.  A
    transcript that is only punctuation or whitespace counts as no transcript:
    nothing would start a turn.  ``0 of 0`` reports ``0.0`` with
    ``measured: 0`` rather than pretending to a rate it never observed.
    """
    clips = list(hypotheses)
    accepted = [
        text for text in clips if normalize_for_eval(text or "").strip()
    ]
    return {
        "clips": len(clips),
        "accepted": len(accepted),
        "false_accept_rate": round(len(accepted) / len(clips), 4) if clips else 0.0,
        "measured": len(clips),
    }


def false_accept_examples(hypotheses: Iterable[str], limit: int = 10) -> list[str]:
    """The transcripts behind the rate — a bare number cannot be diagnosed."""
    return [
        text
        for text in hypotheses
        if normalize_for_eval(text or "").strip()
    ][:limit]
