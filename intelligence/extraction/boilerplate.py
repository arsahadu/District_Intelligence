"""Publish stamps as publication metadata, never as event time."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from intelligence.extraction.normalize import MappedText, normalize
from intelligence.extraction.spans import SourceField, Span, build_evidence_at
from intelligence.models.enums import ExtractionMethod, ScriptType, TextRole, TimeSemantics
from intelligence.models.evidence import Evidence
from intelligence.models.language import TextRepresentation

PROVIDER = "intelligence.extraction.boilerplate"

#: The only semantics this module is ever allowed to claim for these stamps.
BOILERPLATE_SEMANTICS = TimeSemantics.PUBLICATION_TIME

LABELS = ("ADDED", "UPDATED", "PUBLISHED")

TAMIL_MONTHS = (
    "ஜனவரி", "பிப்ரவரி", "மார்ச்", "ஏப்ரல்", "செப்டம்பர்", "அக்டோபர்", "நவம்பர்", "டிசம்பர்",
    "ஆகஸ்ட்", "ஜூலை", "ஜூன்", "மே",
    "ஜன", "பிப்", "மார்", "ஏப்", "ஆக", "செப்", "அக்", "நவ", "டிச", "ஜூ",
)
LATIN_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
)

_MONTH = "|".join(
    sorted((*TAMIL_MONTHS, *LATIN_MONTHS), key=lambda month: (-len(month), month))
)
_DATE = rf"(?:{_MONTH})\.?\s*\d{{1,2}}(?:st|nd|rd|th)?\s*,\s*\d{{4}}"
_TIME = r"\d{1,2}:\d{2}(?:[:.]\d{2})?\s*(?:AM|PM|A\.M\.|P\.M\.)?"
_TIMESTAMP = rf"(?:{_DATE}(?:\s+{_TIME})?|{_TIME})"

BOILERPLATE_RE = re.compile(
    rf"(?P<label>{'|'.join(LABELS)})\s*[:：]\s*(?:(?P<timestamp>{_TIMESTAMP}))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Boilerplate:
    """One publish stamp, recorded as printed text rather than as a time."""

    label: str
    raw_text: str
    char_start: int
    char_end: int
    timestamp_text: Optional[str] = None

    @property
    def semantics(self) -> TimeSemantics:
        return BOILERPLATE_SEMANTICS

    def span(self) -> Span:
        return Span(quote=self.raw_text, char_start=self.char_start, char_end=self.char_end)

    def timestamp_span(self) -> Optional[Span]:
        if self.timestamp_text is None:
            return None
        offset = self.raw_text.index(self.timestamp_text)
        return Span(
            quote=self.timestamp_text,
            char_start=self.char_start + offset,
            char_end=self.char_start + offset + len(self.timestamp_text),
        )

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "raw_text": self.raw_text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "timestamp_text": self.timestamp_text,
            "semantics": self.semantics.value,
        }


def find_boilerplate(text: str) -> list[Boilerplate]:
    """Every publish stamp in ``text``, located in the text exactly as stored."""
    items: list[Boilerplate] = []
    for match in BOILERPLATE_RE.finditer(text):
        items.append(
            Boilerplate(
                label=match.group("label").upper(),
                raw_text=match.group(0),
                char_start=match.start(),
                char_end=match.end(),
                timestamp_text=match.group("timestamp"),
            )
        )
    return items


def _past_stamp(text: str, end: int) -> int:
    while end < len(text) and text[end].isspace():
        end += 1
    return end


def drop_ranges(text: str, items: Sequence[Boilerplate]) -> list[tuple[int, int]]:
    """Stamp ranges extended over the whitespace that follows each stamp."""
    return [(item.char_start, _past_stamp(text, item.char_end)) for item in items]


def body_without_stamps(
    field: SourceField,
    items: Sequence[Boilerplate],
    *,
    lowercase: bool = False,
    **normalize_options,
) -> MappedText:
    return normalize(
        field.text,
        lowercase=lowercase,
        drop_ranges=drop_ranges(field.text, items),
        **normalize_options,
    )


@dataclass(frozen=True)
class BodySplit:
    """Stamps and body kept apart, with the body still traceable to the field."""

    source: SourceField
    items: tuple[Boilerplate, ...]
    body: MappedText

    @property
    def has_boilerplate(self) -> bool:
        return bool(self.items)

    @property
    def time_semantics(self) -> TimeSemantics:
        return BOILERPLATE_SEMANTICS

    def stamps_as_dicts(self) -> list[dict]:
        return [item.as_dict() for item in self.items]

    def timestamp_evidence(self) -> list[Evidence]:
        """Evidence pinning each printed stamp in the original field."""
        produced: list[Evidence] = []
        for item in self.items:
            span = item.timestamp_span()
            if span is None:
                continue
            produced.append(
                build_evidence_at(
                    self.source,
                    span,
                    method=ExtractionMethod.REGEX,
                    notes=f"publication {item.label.lower()} stamp, not event time",
                )
            )
        return produced

    def representation(
        self,
        *,
        representation_id: str,
        derived_from: str,
        language: str,
        script: ScriptType,
        evidence_ids: Iterable[str] = (),
        method: ExtractionMethod = ExtractionMethod.RULE,
    ) -> TextRepresentation:
        return self.body.to_representation(
            representation_id=representation_id,
            derived_from=derived_from,
            language=language,
            script=script,
            role=TextRole.NORMALIZED,
            provider=PROVIDER,
            method=method,
            evidence_ids=evidence_ids,
        )


def split(field: SourceField, **normalize_options) -> BodySplit:
    """Separate a field into its publish stamps and its stamp-free body."""
    items = find_boilerplate(field.text)
    return BodySplit(
        source=field,
        items=tuple(items),
        body=body_without_stamps(field, items, **normalize_options),
    )
