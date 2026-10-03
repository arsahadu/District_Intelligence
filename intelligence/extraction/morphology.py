"""Tamil case and postposition suffix candidates."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional, Sequence

from intelligence.extraction.spans import Span

#: ``(printed suffix, grammatical label)``, spelled with vowel signs as it prints.
SUFFIX_RULES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("த்திலிருந்து", "ablative"),
            ("க்களுக்கு", "dative plural"),
            ("லிருந்து", "ablative"),
            ("ிலிருந்து", "ablative"),
            ("களுக்கு", "dative plural"),
            ("த்திற்கு", "dative"),
            ("க்களில்", "locative plural"),
            ("களில்", "locative plural"),
            ("க்களால்", "instrumental plural"),
            ("களால்", "instrumental plural"),
            ("க்களை", "accusative plural"),
            ("களை", "accusative plural"),
            ("க்கள்", "nominative plural"),
            ("க்காக", "purposative"),
            ("கள்", "nominative plural"),
            ("த்தில்", "locative"),
            ("த்தின்", "genitive"),
            ("த்தால்", "instrumental"),
            ("த்தை", "accusative"),
            ("த்தாக", "predlicative"),
            ("உள்ளிட்ட", "inclusive postposition"),
            ("யில்", "locative"),
            ("யின்", "genitive"),
            ("யால்", "instrumental"),
            ("யை", "accusative"),
            ("மூலம்", "instrumental postposition"),
            ("சார்ந்த", "relational postposition"),
            ("போது", "temporal postposition"),
            ("ளுடன்", "comitative"),
            ("ிடம்", "dative locative"),
            ("மீது", "superessive"),
            ("ுடன்", "comitative"),
            ("ோடு", "comitative"),
            ("ில்", "locative"),
            ("க்கு", "dative"),
            ("ால்", "instrumental"),
            ("ாக", "predlicative"),
            ("ின்", "genitive"),
            ("கு", "dative"),
            ("ல்", "locative"),
        ),
        key=lambda rule: (-len(rule[0]), rule[0]),
    )
)

#: Written as the final two code points of a stem, not as one character.
FINAL_AM = "ம்"
FINAL_AI = "ை"
PULLI = "்"
RESTORED_SUFFIX_PREFIX = "த்த"

#: A vowel-sign suffix swallows the stem's pulli: ``கமிஷனரிடம்``, not ``கமிஷனரிஇடம்``.
VOWEL_SIGNS = frozenset("ாிீுூெேைொோௌ")
VOWEL_CODE_POINTS = frozenset("அஆஇஈஉஊஎஏஐஒஓஔ") | VOWEL_SIGNS

#: A stem shorter than this is not evidence of a stem; it is a fragment.
MIN_STEM_LENGTH = 2


class MorphologyError(ValueError):
    """The requested word could not be analysed as printed."""


@dataclass(frozen=True)
class SuffixCandidate:
    """One reading of a word as a stem plus a suffix."""

    form: str
    stem: str
    suffix: str
    label: str
    rule: str

    @property
    def is_bare_stem(self) -> bool:
        return not self.suffix

    def as_dict(self) -> dict:
        return {
            "form": self.form,
            "stem": self.stem,
            "suffix": self.suffix,
            "label": self.label,
            "rule": self.rule,
        }


def _is_word_character(character: str) -> bool:
    return unicodedata.category(character)[0] in ("L", "M", "N")


def tokenize(text: str) -> list[Span]:
    """Split on anything that is not a letter, mark or digit."""
    tokens: list[Span] = []
    index = 0
    while index < len(text):
        if not _is_word_character(text[index]):
            index += 1
            continue
        end = index
        while end < len(text) and _is_word_character(text[end]):
            end += 1
        tokens.append(Span(quote=text[index:end], char_start=index, char_end=end))
        index = end
    return tokens


def suffix_candidates(word: str, *, min_stem_length: int = MIN_STEM_LENGTH) -> list[SuffixCandidate]:
    """Every way this printed word could be a stem plus a Tamil suffix."""
    if not word:
        raise MorphologyError("an empty word has no suffix analysis")

    candidates: list[SuffixCandidate] = []
    seen: set[tuple[str, str]] = set()
    for suffix, label in SUFFIX_RULES:
        if not word.endswith(suffix) or len(word) - len(suffix) < min_stem_length:
            continue
        stem = word[: len(word) - len(suffix)]
        additions = [SuffixCandidate(word, stem, suffix, label, "strip-suffix")]
        if suffix.startswith(RESTORED_SUFFIX_PREFIX):
            additions.append(
                SuffixCandidate(word, stem + FINAL_AM, suffix, label, "strip-suffix+restore-am")
            )
        if suffix[0] in VOWEL_SIGNS:
            additions.append(
                SuffixCandidate(
                    word, stem + PULLI, suffix, label, "strip-suffix+restore-pulli"
                )
            )
        for candidate in additions:
            key = (candidate.stem, candidate.suffix)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)

    candidates.append(SuffixCandidate(word, word, "", "none", "as-printed"))
    return candidates


def _stem_bases(stem: str) -> list[tuple[str, str]]:
    """Written forms a suffix attaches to; a final ஂ always drops, so never with it."""
    if stem.endswith(FINAL_AM):
        return [(stem[: -len(FINAL_AM)], "concatenate+drop-am")]
    if stem.endswith(PULLI):
        return [(stem, "concatenate"), (stem[: -len(PULLI)], "concatenate+drop-pulli")]
    return [(stem, "concatenate")]


def surface_forms(stem: str) -> list[SuffixCandidate]:
    """Printed forms this dictionary stem may appear as."""
    if not stem:
        raise MorphologyError("an empty stem has no surface forms")

    forms = [SuffixCandidate(stem, stem, "", "none", "dictionary-form")]
    seen = {stem}
    for base, rule in _stem_bases(stem):
        for suffix, label in SUFFIX_RULES:
            if base.endswith(FINAL_AI) and suffix[0] in VOWEL_CODE_POINTS:
                continue
            form = base + suffix
            if form not in seen:
                seen.add(form)
                forms.append(SuffixCandidate(form, stem, suffix, label, rule))
    return forms


def matches_surface(word: str, stem: str) -> Optional[SuffixCandidate]:
    """The reading of ``word`` that ``stem`` predicts, or None if it does not."""
    for candidate in surface_forms(stem):
        if candidate.form == word:
            return candidate
    return None


def analyze_tokens(text: str, *, min_stem_length: int = MIN_STEM_LENGTH) -> dict[str, list[SuffixCandidate]]:
    """Suffix candidates for every distinct printed token in ``text``."""
    return {
        token.quote: suffix_candidates(token.quote, min_stem_length=min_stem_length)
        for token in tokenize(text)
    }
