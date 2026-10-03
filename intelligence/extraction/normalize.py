"""Derived text that still knows the original range of every character."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from intelligence.extraction.spans import Span, SourceField, build_evidence_at, find_spans, locate
from intelligence.models.base import OptionalConfidence
from intelligence.models.enums import ExtractionMethod, ScriptType, TextRole
from intelligence.models.evidence import Evidence
from intelligence.models.language import TextRepresentation

PROVIDER = "intelligence.extraction.normalize"

#: Tamil vowel signs are Mc with combining class 0, so ccc alone cannot cluster.
COMBINING_CATEGORIES = frozenset({"Mn", "Mc", "Me"})


class MappingError(ValueError):
    """An edit could not be tracked back to a range in the original text."""


def cluster_end(text: str, start: int) -> int:
    """End of the grapheme starting at ``start`` (base code point plus its marks)."""
    if start >= len(text):
        raise MappingError(f"index {start} is past the end of a {len(text)} character text")
    end = start + 1
    while end < len(text) and unicodedata.category(text[end]) in COMBINING_CATEGORIES:
        end += 1
    return end


def build_mapped_text(
    original: str,
    pieces: Sequence[tuple[str, int, int]],
    steps: Sequence[str] = (),
) -> MappedText:
    """Assemble derived text from left-to-right ``(output, start, end)`` pieces."""
    characters: list[str] = []
    positions: list[tuple[int, int]] = []
    cursor = 0
    for text, start, end in pieces:
        if start < cursor or end < start or end > len(original):
            raise MappingError(
                f"piece {start}:{end} overlaps or runs past the previous edit at {cursor}"
            )
        characters.append(text)
        positions.extend(((start, end),) * len(text))
        cursor = end
    return MappedText(
        original=original,
        text="".join(characters),
        positions=tuple(positions),
        steps=tuple(steps),
    )


@dataclass(frozen=True)
class MappedText:
    """Derived text plus the original range of every character it holds."""

    original: str
    text: str
    positions: tuple[tuple[int, int], ...]
    steps: tuple[str, ...] = ()

    @property
    def offsets_align_with_source(self) -> bool:
        return self.text == self.original

    def source_range(self, index: int) -> tuple[int, int]:
        if not 0 <= index < len(self.text):
            raise MappingError(f"derived index {index} is outside 0:{len(self.text)}")
        return self.positions[index]

    def project(self, start: int, end: int) -> Span:
        """Turn a range in derived coordinates into a span of the original text."""
        if not 0 <= start < end <= len(self.text):
            raise MappingError(f"derived range {start}:{end} is outside 0:{len(self.text)}")
        char_start = self.positions[start][0]
        char_end = self.positions[end - 1][1]
        quote = self.original[char_start:char_end]
        if not quote:
            raise MappingError(f"derived range {start}:{end} maps to nothing in the original")
        return Span(quote=quote, char_start=char_start, char_end=char_end)

    def spans_for(self, term: str) -> list[Span]:
        return [
            self.project(span.char_start, span.char_end)
            for span in find_spans(self.text, term)
        ]

    def span_for(self, term: str, *, occurrence: Optional[int] = None) -> Span:
        found = locate(self.text, term, occurrence=occurrence)
        return self.project(found.char_start, found.char_end)

    def evidence(
        self,
        source: SourceField,
        term: str,
        *,
        method: ExtractionMethod,
        occurrence: Optional[int] = None,
        confidence: OptionalConfidence = None,
        evidence_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Evidence:
        """Evidence for a term found in derived text, anchored in the original."""
        self.assert_same_field(source)
        return self.evidence_at(
            source,
            self.span_for(term, occurrence=occurrence),
            method=method,
            confidence=confidence,
            evidence_id=evidence_id,
            notes=notes,
        )

    def evidence_at(
        self,
        source: SourceField,
        span: Span,
        *,
        method: ExtractionMethod,
        confidence: OptionalConfidence = None,
        evidence_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Evidence:
        self.assert_same_field(source)
        return build_evidence_at(
            source,
            span,
            method=method,
            confidence=confidence,
            evidence_id=evidence_id,
            notes=notes,
        )

    def assert_same_field(self, source: SourceField) -> None:
        if source.text != self.original:
            raise MappingError(
                f"{source.describe()} does not hold the text these offsets were derived "
                "from; spans are cut from the source field, never from a derived copy"
            )

    def to_representation(
        self,
        *,
        representation_id: str,
        derived_from: str,
        language: str,
        script: ScriptType,
        role: TextRole = TextRole.NORMALIZED,
        provider: str = PROVIDER,
        provider_version: Optional[str] = None,
        method: ExtractionMethod = ExtractionMethod.RULE,
        confidence: OptionalConfidence = None,
        evidence_ids: Iterable[str] = (),
    ) -> TextRepresentation:
        if not self.text:
            raise MappingError(
                f"derivation {self.steps or ('identity',)} produced empty text; "
                "a representation must hold something"
            )
        return TextRepresentation(
            representation_id=representation_id,
            text=self.text,
            role=role,
            language=language,
            script=script,
            derived_from=derived_from,
            provider=provider,
            provider_version=provider_version,
            offsets_align_with_source=self.offsets_align_with_source,
            method=method,
            confidence=confidence,
            evidence_ids=list(evidence_ids),
        )


def _dropped_indices(text: str, drop_ranges: Iterable[tuple[int, int]]) -> frozenset[int]:
    ranges = []
    for start, end in drop_ranges:
        if not 0 <= start <= end <= len(text):
            raise MappingError(f"drop range {start}:{end} is outside a {len(text)} character field")
        ranges.append((start, end))
    ranges.sort()
    previous_end = 0
    for start, end in ranges:
        if start < previous_end:
            raise MappingError(f"drop ranges overlap at {start}:{end}")
        previous_end = end
    return frozenset(
        index for start, end in ranges for index in range(start, end)
    )


def normalize(
    original: str,
    *,
    unicode_form: Optional[str] = "NFC",
    lowercase: bool = True,
    collapse_whitespace: bool = True,
    drop_ranges: Iterable[tuple[int, int]] = (),
) -> MappedText:
    """Build a normalized copy of ``original`` without touching ``original``."""
    dropped = _dropped_indices(original, drop_ranges)
    steps: list[str] = []
    if unicode_form:
        steps.append(f"unicode:{unicode_form}")
    if lowercase:
        steps.append("lowercase")
    if collapse_whitespace:
        steps.append("collapse_whitespace")
    if dropped:
        steps.append(f"drop:{len(dropped)}")

    pieces: list[tuple[str, int, int]] = []
    index = 0
    while index < len(original):
        if index in dropped:
            index += 1
            continue
        character = original[index]
        if character.isspace():
            end = index
            while end < len(original) and original[end].isspace() and end not in dropped:
                end += 1
            if collapse_whitespace:
                pieces.append((" ", index, end))
            else:
                pieces.extend((original[i], i, i + 1) for i in range(index, end))
            index = end
            continue
        end = cluster_end(original, index)
        chunk = original[index:end]
        if unicode_form:
            chunk = unicodedata.normalize(unicode_form, chunk)
        if lowercase:
            chunk = chunk.lower()
        pieces.append((chunk, index, end))
        index = end
    return build_mapped_text(original, pieces, steps)


def normalize_field(source: SourceField, **options) -> MappedText:
    return normalize(source.text, **options)
