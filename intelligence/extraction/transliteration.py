"""Tamil-to-Latin candidates from a script table plus a curated vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from intelligence.extraction import morphology
from intelligence.extraction.normalize import (
    MappedText,
    build_mapped_text,
    cluster_end,
)
from intelligence.extraction.spans import SourceField
from intelligence.models.base import OptionalConfidence
from intelligence.models.enums import ExtractionMethod, ScriptType, TextRole
from intelligence.models.language import TextRepresentation

PROVIDER = "intelligence.extraction.transliteration"

CONSONANT_LATIN = {
    "க": "k",
    "ங": "ng",
    "ச": "s",
    "ஞ": "nj",
    "ட": "t",
    "ண": "n",
    "த": "th",
    "ந": "n",
    "ப": "p",
    "ம": "m",
    "ய": "y",
    "ர": "r",
    "ல": "l",
    "வ": "v",
    "ழ": "zh",
    "ள": "l",
    "ற": "r",
    "ன": "nn",
    "ஜ": "j",
    "ஷ": "sh",
    "ஸ": "s",
    "ஹ": "h",
}

VOWEL_LATIN = {
    "அ": "a",
    "ஆ": "aa",
    "இ": "i",
    "ஈ": "ee",
    "உ": "u",
    "ஊ": "oo",
    "எ": "e",
    "ஏ": "ee",
    "ஐ": "ai",
    "ஒ": "o",
    "ஓ": "oo",
    "ஔ": "au",
}

SIGN_LATIN = {
    "ா": "aa",
    "ி": "i",
    "ீ": "ee",
    "ு": "u",
    "ூ": "oo",
    "ெ": "e",
    "ே": "ee",
    "ை": "ai",
    "ொ": "o",
    "ோ": "oo",
    "ௌ": "au",
    "்": "",
    "ஂ": "m",
    "ஃ": "h",
}
PULLI = "்"

@dataclass(frozen=True)
class VocabularyTerm:
    source: str
    latins: tuple[str, ...]
    note: str


VOCABULARY: dict[str, VocabularyTerm] = {
    term.source: term
    for term in (
        VocabularyTerm("கமிஷனர்", ("Commissioner",), "loanword spelling"),
        VocabularyTerm("கலெக்டர்", ("Collector",), "loanword spelling"),
        VocabularyTerm("லேண்ட்", ("Land",), "loanword spelling"),
        VocabularyTerm("இன்சுரன்ஸ்", ("Insurance",), "loanword spelling"),
        VocabularyTerm("மதுரை", ("Madurai",), "place name"),
        VocabularyTerm("கும்பகோணம்", ("Kumbakonam",), "place name"),
        VocabularyTerm("சென்னை", ("Chennai",), "place name"),
        VocabularyTerm("தஞ்சாவூர்", ("Thanjavur",), "place name"),
        VocabularyTerm("சிவகங்கை", ("Sivaganga",), "place name"),
        VocabularyTerm("மாநகராட்சி", ("Managaram",), "press spelling"),
    )
}


def render_cluster(cluster: str) -> str:
    """Latin rendering of one base code point plus its combining marks."""
    if not cluster:
        return ""
    base, marks = cluster[0], cluster[1:]
    rendered = "".join(SIGN_LATIN.get(mark, mark) for mark in marks)
    if base in CONSONANT_LATIN:
        if not marks or all(mark == PULLI for mark in marks):
            return CONSONANT_LATIN[base] + ("a" if not marks else "")
        return CONSONANT_LATIN[base] + rendered
    if base in VOWEL_LATIN:
        return VOWEL_LATIN[base] + rendered
    return cluster


def transliterate(text: str) -> MappedText:
    """A Latin-script rendering of ``text`` that still knows where it came from."""
    pieces: list[tuple[str, int, int]] = []
    index = 0
    while index < len(text):
        end = cluster_end(text, index)
        pieces.append((render_cluster(text[index:end]), index, end))
        index = end
    return build_mapped_text(text, pieces, steps=("tamil-transliteration",))


@dataclass(frozen=True)
class TransliterationCandidate:
    """One Latin spelling suggested for one printed Tamil word."""

    form: str
    latin: str
    basis: str
    method: ExtractionMethod
    note: Optional[str] = None
    stem: Optional[str] = None

    @property
    def is_from_vocabulary(self) -> bool:
        return self.method is ExtractionMethod.DICTIONARY

    def as_dict(self) -> dict:
        return {
            "form": self.form,
            "latin": self.latin,
            "basis": self.basis,
            "method": self.method.value,
            "note": self.note,
            "stem": self.stem,
        }


def vocabulary_term(word: str) -> Optional[VocabularyTerm]:
    return VOCABULARY.get(word)


def candidates(word: str) -> list[TransliterationCandidate]:
    """Latin spellings to consider for this printed word, strongest first."""
    if not word:
        raise ValueError("an empty word has no transliteration")

    found: list[TransliterationCandidate] = []
    term = vocabulary_term(word)
    if term is not None:
        found.extend(
            TransliterationCandidate(word, latin, "vocabulary", ExtractionMethod.DICTIONARY, term.note)
            for latin in term.latins
        )

    for suffix_candidate in morphology.suffix_candidates(word):
        if suffix_candidate.is_bare_stem:
            continue
        stemmed = vocabulary_term(suffix_candidate.stem)
        if stemmed is None:
            continue
        found.extend(
            TransliterationCandidate(
                word,
                latin,
                "vocabulary of stem",
                ExtractionMethod.DICTIONARY,
                stemmed.note,
                stem=suffix_candidate.stem,
            )
            for latin in stemmed.latins
        )
        break

    table = transliterate(word).text
    if table != word:
        found.append(
            TransliterationCandidate(word, table, "table", ExtractionMethod.RULE, None)
        )

    unique: list[TransliterationCandidate] = []
    seen: set[str] = set()
    for candidate in found:
        key = candidate.latin.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def candidates_for_text(text: str) -> dict[str, list[TransliterationCandidate]]:
    """Transliteration candidates for every distinct printed token in ``text``."""
    return {token.quote: candidates(token.quote) for token in morphology.tokenize(text)}


def representation(
    mapped: MappedText,
    *,
    representation_id: str,
    derived_from: str,
    language: str,
    evidence_ids: Iterable[str] = (),
    confidence: OptionalConfidence = None,
) -> TextRepresentation:
    """A TRANSLITERATED representation: the same words, a different script."""
    return mapped.to_representation(
        representation_id=representation_id,
        derived_from=derived_from,
        language=language,
        script=ScriptType.LATIN,
        role=TextRole.TRANSLITERATED,
        provider=PROVIDER,
        method=ExtractionMethod.RULE,
        confidence=confidence,
        evidence_ids=evidence_ids,
    )


def transliterated_field(source: SourceField) -> MappedText:
    return transliterate(source.text)
