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
ZWJ = "\u200d"
TATWEEL = "\u0640"

#: Arabic letter forms that Persian types differently, plus the hamza-bearing
#: alef forms.  Persian writes ``ا`` where Arabic writes ``أ``/``إ``/``ٱ``: the
#: glottal stop is the same phoneme, so keeping the Arabic form meant the same
#: spoken word scored as an error (and reached Piper as a character the voice
#: was not trained on).  ``ؤ``/``ئ`` are *not* mapped — Persian keeps them
#: (``مؤمن``, ``مسئول``) and dropping the hamza would change the word.
LETTER_MAP = {
    "\u064a": "\u06cc",  # ARABIC YEH        ي → ی
    "\u0649": "\u06cc",  # ALEF MAKSURA      ى → ی
    "\u0643": "\u06a9",  # ARABIC KAF        ك → ک
    "\u0629": "\u0647",  # TEH MARBUTA       ة → ه
    "\u0623": "\u0627",  # ALEF WITH HAMZA ABOVE   أ → ا
    "\u0625": "\u0627",  # ALEF WITH HAMZA BELOW   إ → ا
    "\u0671": "\u0627",  # ALEF WASLA              ٱ → ا
    # HEH WITH YEH ABOVE (ۀ, the ezafe after a silent ه) has no Persian
    # letter of its own: it is written ه + U+0654 (see ``prepare_for_tts``,
    # which restores that form for the voice before this map runs).
    "\u06c0": "\u0647",
}

#: Invisible formatting characters that carry no speech (bidi marks, zero-width
#: space, word joiner, soft hyphen, BOM).  ``ZWNJ`` is deliberately *not* here:
#: نیم‌فاصله is orthography and has its own policy.  ``ZWJ`` is removed because
#: Persian only uses it to force a joined form that a reader never pronounces.
INVISIBLE_CHARS = frozenset(
    "\u00ad\u061c\u200b\u200d\u200e\u200f"
    "\u202a\u202b\u202c\u202d\u202e"
    "\u2060\u2066\u2067\u2068\u2069\ufeff"
)

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
#: ``٪`` (U+066A, ARABIC PERCENT SIGN) and ``%`` are the same symbol written from
#: two keyboards.  Unifying them keeps ``۲۵٪`` and ``۲۵%`` from scoring as an
#: error against each other while both stay punctuation (``normalize_for_eval``
#: deletes them, so ``درصد`` in words is what the comparison sees).
_PERCENT_SIGN = "\u066a"


def _strip_invisible(value: str) -> str:
    """Remove bidi marks, zero-width space/joiner, soft hyphen and the BOM.

    These are pasted in from PDFs and web pages and are invisible to a human, so
    they cannot be spotted in a diff.  ``ZWNJ`` is *not* removed — see
    :data:`INVISIBLE_CHARS`.
    """
    return "".join(char for char in value if char not in INVISIBLE_CHARS)


def _clean_zwnj(value: str, policy: str) -> str:
    """Apply a ZWNJ policy and drop the ZWNJs that are not نیم‌فاصله.

    A نیم‌فاصله only exists *between two letters*, so a ZWNJ at the start or end
    of the text, next to a space/punctuation, or doubled is a copy-paste artefact
    rather than orthography.  Keeping it would put an invisible character inside
    a token: ``" ".join(value.split())`` does not split on U+200C (it is not
    whitespace), so ``"می {ZWNJ}روم"`` used to stay one "word" with a stray
    mark even after whitespace collapse.
    """
    value = value.replace(ZWJ, "")
    if policy == "keep":
        value = re.sub(rf"(?<![\w]){ZWNJ}+|{ZWNJ}+(?![\w])", "", value)
        return re.sub(rf"{ZWNJ}{{2,}}", ZWNJ, value)
    if policy == "space":
        return re.sub(rf"\s*{ZWNJ}\s*", " ", value)
    return value.replace(ZWNJ, "")


#: A run of digits with the separators that appear *inside* a number: the Persian
#: decimal ``٫`` (U+066B) and thousands ``٬`` (U+066C), plus ASCII ``,`` and ``.``.
#: The Arabic comma ``،`` is deliberately excluded — it separates items, not
#: digits, so ``۳،۵`` is two numbers and never "3.5".
_NUMBER_RUN_RE = re.compile(
    r"[0-9\u0660-\u0669\u06f0-\u06f9]+(?:[.,\u066b\u066c][0-9\u0660-\u0669\u06f0-\u06f9]+)*"
)


def _unify_numeric_separators(value: str) -> str:
    """Rewrite every number to one canonical form (ASCII digits, ``.`` decimal).

    ``normalize_for_eval`` is the WER policy and must not turn a correct number
    into a different one.  Before this, punctuation removal *deleted* ``٫`` and
    ``:``, so ``۳٫۵`` became ``35`` and ``۳:۳۰`` became ``330``: deleting a
    separator is indistinguishable from deleting the distinction it encodes.

    Each digit run is canonicalised with :func:`_normalise_separators` — the same
    function that decides what the voice reads — so ``۳٫۵``/``3,5``/``3.5`` all
    become ``3.5`` and ``۱٬۲۰۰``/``1,200``/``1.200`` all become ``1200``.
    Separators *outside* a number are left for the punctuation step, which turns
    them into a space (never into nothing, which used to glue two words).
    """

    def canonical(match: re.Match[str]) -> str:
        digits = "".join(DIGIT_MAP.get(char, char) for char in match.group(0))
        return _normalise_separators(digits)

    return _NUMBER_RUN_RE.sub(canonical, value)


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
    ``punctuation`` — ``True`` replaces punctuation with a space.  It is a
    *space*, not a deletion: deleting ``.``/``:``/``٫`` cannot be told apart from
    deleting a digit separator, and it glued ``۳.۵`` into ``35``.
    """
    if zwnj not in {"keep", "space", "remove"}:
        raise ValueError("zwnj must be 'keep', 'space' or 'remove'")
    if digits not in {"ascii", "keep"}:
        raise ValueError("digits must be 'ascii' or 'keep'")

    value = unicodedata.normalize("NFC", text or "")
    value = _strip_invisible(value)
    if strip_diacritics:
        value = "".join(char for char in value if char not in DIACRITICS)
    value = value.replace(TATWEEL, "")
    if map_letters:
        value = "".join(LETTER_MAP.get(char, char) for char in value)
    value = value.replace(_ARABIC_QUESTION, "؟").replace(_PERCENT_SIGN, "%")
    if digits == "ascii":
        value = "".join(DIGIT_MAP.get(char, char) for char in value)
    value = _clean_zwnj(value, zwnj)
    if punctuation:
        value = "".join(" " if char in PUNCTUATION else char for char in value)
    return " ".join(value.split())


def normalize_for_eval(text: str) -> str:
    """The evaluation policy used for every WER/CER figure in this project.

    Punctuation *and digit separators* become spaces before punctuation is
    handled, so a correct ``۳٫۵ متر`` and ``3.5 متر`` still compare equal to
    ``سه ممیز پنج``-shaped variants instead of collapsing into ``35``.  See
    ``docs/BENCHMARKS.md`` for the policy this implements.
    """
    value = _unify_numeric_separators(text or "")
    return normalize(value, zwnj="space", digits="ascii", punctuation=True)


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
        # ``str.isdigit`` is True for superscripts (``²``) that ``int`` refuses,
        # which raised ValueError on ordinary pasted text.
        if token.isdecimal() and all(
            char.isascii() or char in DIGIT_MAP for char in token
        ):
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
#: sentence.  ``1.5`` (a decimal) must not match, hence the lookahead — and the
#: digits include the Persian/Arabic-Indic ones, because a Persian model writes
#: ``۱.`` far more often than ``1.``.  That omission read each marker aloud as
#: "یک." ("one, full stop") before the item.
_LIST_MARKER_RE = re.compile(
    r"^[ \t]*(?:[\d\u0660-\u0669\u06f0-\u06f9]{1,3}[.)](?=\s)"
    r"|[•▪◦‣∙·*+\-–—](?=\s))\s*",
    re.M,
)

# ``ساعت`` is part of the match so "ساعت 10:30" does not become
# "ساعت ساعت ده و سی دقیقه", and a bare "10:30" still gets its unit word.
#: Only ``:`` is a clock separator.  ``٫`` is the *decimal* mark, so including
#: it read ``۳٫۴۵`` as "سه و چهل و پنج دقیقه" instead of "سه ممیز چهل و پنج".
_TIME_RE = re.compile(
    r"(?:(?P<word>ساعت)\s*)?\b(?P<hour>[01]?\d|2[0-3])\s*:\s*(?P<minute>[0-5]\d)\b"
)
_PERCENT_RE = re.compile(r"(?P<number>[0-9][0-9.,٫٬]*)\s*(?:[٪%]|درصد)")
#: ``٪۲۵`` — the sign before the number.  Without this the mark reached
#: Piper, which reads it as a hesitation before "بیست و پنج".
_PERCENT_BEFORE_RE = re.compile(r"[٪%]\s*(?P<number>[0-9][0-9.,٫٬]*)")
_CURRENCY_AFTER_RE = re.compile(
    r"(?P<number>[0-9][0-9.,٫٬]*)\s*(?P<unit>تومان|تومان‌ها|ریال|دلار|یورو|پوند|﷼)"
)
_CURRENCY_BEFORE_RE = re.compile(r"(?P<unit>[$€£])\s*(?P<number>[0-9][0-9.,٫٬]*)")
# ``5$`` is clockwise English, ``$5`` counter-clockwise; both are read aloud.
_CURRENCY_SUFFIX_RE = re.compile(r"(?P<number>[0-9][0-9.,٫٬]*)\s*(?P<unit>[$€£])")
# A number, optionally with a thousands group and a decimal part, in either the
# Persian or the ASCII convention.  Matched as a *whole* so ``1,234.5`` is read
# as one quantity instead of ``234.5`` plus a stranded ``1,``.
_NUMBER_RE = re.compile(
    r"[0-9]{1,3}(?:[.,٫٬][0-9]{3})+(?:[.,٫٬][0-9]+)?"
    r"|[0-9]+(?:[.,٫٬][0-9]+)?"
)
#: Brackets and stray glue that a voice would read as noise.
_NOISE_CHARS_RE = re.compile(r"[\[\]{}()<>|^~\\]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_DECIMAL_SEP = "٫"  # U+066B, the Persian decimal separator Piper voices know
_THOUSANDS_SEP = "٬"  # U+066C, the Persian thousands separator

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


def _strip_group_separators(value: str) -> str:
    """Drop the ``,``/``.``/``٬`` that group thousands: ``1,234`` → ``1234``."""
    return value.replace(",", "").replace(".", "").replace(_THOUSANDS_SEP, "")


def _normalise_separators(value: str) -> str:
    """Decide which separators are thousands groups and which are decimals.

    Persian writes decimals with ``٫`` (U+066B) and thousands with ``٬``
    (U+066C); text pasted from elsewhere mixes those with ``,`` and ``.``.  The
    two Persian characters are unambiguous and are handled first, which is what
    used to go wrong: ``۱٬۲۰۰٬۰۰۰`` was read as "یک٬دویست٬صفر" because the
    thousands mark was never removed.

    Only ``,``/``.`` remain ambiguous, and they are decided by the digits that
    follow:

    * ``1,234.5`` → ``1234.5`` (a three-digit group, then a fractional part);
    * ``1.234.567`` → ``1234567`` (repeated three-digit groups are thousands);
    * ``1,5`` → ``1.5``, ``3.5`` → ``3.5`` (one or two digits is a fraction);
    * ``0.125`` → ``125``: three digits after a single separator is read as a
      thousands group, the same rule the voice used before.  A caller that means
      ``0.125`` must write ``۰٫۱۲۵`` (the Persian decimal mark) to say so.
    """
    value = _to_ascii_digits(value or "").strip()
    if not value or re.fullmatch(r"[0-9.," + _DECIMAL_SEP + _THOUSANDS_SEP + "]*", value) is None:
        return value
    whole, mark, fraction = value.partition(_DECIMAL_SEP)
    if mark:
        # ``٫`` is a decimal mark by definition: what follows is the fraction,
        # and any group separator inside it is a typo, not a grouping.
        fraction = _strip_group_separators(fraction)
        whole = _strip_group_separators(whole)
        return f"{whole}.{fraction}" if fraction else whole
    whole = value.replace(_THOUSANDS_SEP, "")
    groups = re.fullmatch(r"([0-9]{1,3}(?:[.,][0-9]{3})+)(?:[.,]([0-9]+))?", whole)
    if groups:
        joined = _strip_group_separators(groups.group(1))
        return f"{joined}.{groups.group(2)}" if groups.group(2) else joined
    return whole.replace(",", ".")


def _to_ascii_digits(text: str) -> str:
    """Persian (۰-۹) and Arabic-Indic (٠-٩) digits → ASCII."""
    return "".join(DIGIT_MAP.get(char, char) for char in (text or ""))


#: A number with something *inside* it (``۳.۵``, ``10:30``, ``۱٫۵``) must never
#: be a sentence boundary.  Digits of any script, because a Persian model mixes
#: them freely.
_DECIMAL_OR_CLOCK_RE = re.compile(
    r"[\d\u0660-\u0669\u06f0-\u06f9]+(?:[.:٫٬][\d\u0660-\u0669\u06f0-\u06f9]+)+"
)


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

    # Hide decimals and clock times from the sentence splitter.  The digit class
    # is any script, not just ASCII: ``۳.۵`` used to be split into ``۳.`` and
    # ``۵`` -- and with it every Persian list number ``۱.`` into its own
    # utterance, which Piper then read aloud as "یک.".
    value = _DECIMAL_OR_CLOCK_RE.sub(_protect, value)

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
    # ``ۀ`` (U+06C0) is the ezafe written as one letter.  ``LETTER_MAP`` folds it
    # to ``ه`` for evaluation, but a voice needs the hamza: without it "خانۀ من"
    # is "خانه من" (a bare "house my"), not "خانهٔ من" ("the house of mine").
    value = value.replace("\u06c0", "\u0647\u0654")
    value = "".join(LETTER_MAP.get(char, char) for char in value)
    value = value.replace(TATWEEL, "").replace(_ARABIC_QUESTION, "؟")
    value = value.replace(_PERCENT_SIGN, "%")
    value = _to_ascii_digits(value)
    value = _CONTROL_RE.sub(" ", value)
    value = _NOISE_CHARS_RE.sub(" ", value)
    # Invisible paste artefacts (zero-width space, soft hyphen, bidi marks, BOM)
    # and a ZWNJ that is not between two letters: both are unpronounceable and
    # would otherwise reach the voice as a gap in the middle of a word.
    value = _strip_invisible(value)
    value = _clean_zwnj(value, "keep")
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
    value = _PERCENT_BEFORE_RE.sub(_percent, value)
    value = _CURRENCY_AFTER_RE.sub(_currency_after, value)
    value = _CURRENCY_SUFFIX_RE.sub(_currency_before, value)
    value = _CURRENCY_BEFORE_RE.sub(_currency_before, value)
    value = _NUMBER_RE.sub(lambda m: number_to_words(m.group(0)), value)
    # ``_NUMBER_RE`` accepts the Persian marks now, so any that survives is
    # *between* numbers (``۱۲٬۵`` was read as "صد و بیست و پنج"): it is a
    # thousands mark, not something a voice should try to pronounce.
    value = value.replace(_DECIMAL_SEP, " ممیز ").replace(_THOUSANDS_SEP, "")
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
