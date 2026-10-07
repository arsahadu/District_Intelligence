"""Stage 7 cue reading: which printed words carry an event type, and which refuse to carry one."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from intelligence.config import event_type_cues as vocabulary
from intelligence.extraction import mention_text
from intelligence.extraction.mention_text import Entry, Lexicon, Prose, Word
from intelligence.extraction.spans import SourceField, Span
from intelligence.models.enums import ExtractionMethod, EventType
from intelligence.models.evidence import Evidence

PROVIDER = "intelligence.extraction.cues"
METHOD = ExtractionMethod.DICTIONARY

RULE_PRINTED = "as-printed"
RULE_PHRASE = "phrase"
RULE_LATIN = "latin-spelling"
RULE_INFLECTED = "inflected"

#: How sure each reading is that the word printed here is the cue it looks like.
QUALITY = {
    RULE_PRINTED: 0.95,
    RULE_PHRASE: 0.95,
    RULE_LATIN: 0.9,
    RULE_INFLECTED: 0.8,
}

NON_CUE_FOLDED = frozenset(mention_text.fold(word) for word in vocabulary.NON_CUE_WORDS)
NON_EVENT_FOLDED = {mention_text.fold(row.surface): row for row in vocabulary.NON_EVENT_CUES}


def _cue_claim(row) -> tuple[str, ...]:
    return (row.event_type.value, row.strength)


CUE_ENTRIES = tuple(
    Entry(
        surface=cue.surface,
        lexicon="EVENT_TYPE_CUES",
        kind="event-cue",
        claim=_cue_claim(cue),
        latin=cue.latin,
        note=cue.note,
        row=cue,
    )
    for cue in vocabulary.EVENT_TYPE_CUES
)


def _refused(form: str) -> bool:
    return mention_text.fold(form) in NON_CUE_FOLDED


CUE_LEXICON = Lexicon("cue", (("EVENT_TYPE_CUES", CUE_ENTRIES),), _refused)


def _phrase_keys() -> tuple[dict[tuple[str, ...], list[Entry]], tuple[int, ...]]:
    keys: dict[tuple[str, ...], list[Entry]] = {}
    for entry in CUE_ENTRIES:
        for surface in (entry.surface, *entry.latin):
            if " " not in surface.strip():
                continue
            key = tuple(mention_text.fold(part) for part in surface.split())
            keys.setdefault(key, []).append(entry)
    lengths = tuple(sorted({len(key) for key in keys}, reverse=True))
    return keys, lengths


PHRASE_KEYS, PHRASE_LENGTHS = _phrase_keys()


def _single_claim(entries: Sequence[Entry]) -> Optional[Entry]:
    if not entries or len({entry.claim for entry in entries}) > 1:
        return None
    return entries[0]


@dataclass(frozen=True)
class CueMatch:
    """One stretch of one field that names one event type, with the evidence that quotes it."""

    field: str
    event_type: EventType
    strength: str
    entry: str
    surface: str
    rule: str
    via: str
    char_start: int
    char_end: int
    sentence: int
    words: tuple[int, ...]
    note: str
    evidence: Optional[Evidence] = None

    @property
    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    @property
    def quality(self) -> float:
        return QUALITY[self.rule]

    @property
    def decisive(self) -> bool:
        return self.strength == vocabulary.PRIMARY

    @property
    def evidence_id(self) -> str:
        assert self.evidence is not None
        return self.evidence.evidence_id

    def describe(self) -> str:
        return (
            f"{self.event_type.value} <- {self.surface!r} in {self.field} "
            f"[{self.char_start}:{self.char_end}] ({self.strength}, {self.rule})"
        )

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "event_type": self.event_type.value,
            "strength": self.strength,
            "entry": self.entry,
            "surface": self.surface,
            "rule": self.rule,
            "via": self.via,
            "quality": self.quality,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "sentence": self.sentence,
            "words": list(self.words),
            "note": self.note,
            "evidence_id": self.evidence.evidence_id if self.evidence else None,
        }


@dataclass(frozen=True)
class NonEventMatch:
    """A printed word whose whole job is to say nothing happened."""

    field: str
    entry: str
    surface: str
    char_start: int
    char_end: int
    sentence: int
    reason: str
    evidence: Optional[Evidence] = None

    @property
    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    @property
    def evidence_id(self) -> str:
        assert self.evidence is not None
        return self.evidence.evidence_id

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "entry": self.entry,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "reason": self.reason,
            "evidence_id": self.evidence.evidence_id if self.evidence else None,
        }


@dataclass(frozen=True)
class Refusal:
    """A word that looked like it carried an event type and was refused instead."""

    field: str
    surface: str
    char_start: int
    char_end: int
    reason: str

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CueFinding:
    """What one field said about what kind of event this is."""

    field: str
    matches: tuple[CueMatch, ...] = ()
    non_events: tuple[NonEventMatch, ...] = ()
    refusals: tuple[Refusal, ...] = ()

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "matches": [match.as_dict() for match in self.matches],
            "non_events": [marker.as_dict() for marker in self.non_events],
            "refusals": [refusal.as_dict() for refusal in self.refusals],
        }


@dataclass(frozen=True)
class CueExtraction:
    """Every cue this record printed, across every field its policy declared readable."""

    record_id: str
    findings: tuple[CueFinding, ...] = ()

    @property
    def matches(self) -> tuple[CueMatch, ...]:
        return tuple(match for finding in self.findings for match in finding.matches)

    @property
    def non_events(self) -> tuple[NonEventMatch, ...]:
        return tuple(marker for finding in self.findings for marker in finding.non_events)

    @property
    def refusals(self) -> tuple[Refusal, ...]:
        return tuple(refusal for finding in self.findings for refusal in finding.refusals)

    def matches_for(self, event_type: EventType) -> tuple[CueMatch, ...]:
        return tuple(match for match in self.matches if match.event_type is event_type)

    def surfaces_for(self, event_type: EventType) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for match in self.matches_for(event_type):
            seen.setdefault(mention_text.fold(match.entry), None)
        return tuple(seen)

    def evidence(self) -> tuple[Evidence, ...]:
        collected: dict[str, Evidence] = {}
        for item in (*self.matches, *self.non_events):
            if item.evidence is not None:
                collected.setdefault(item.evidence.evidence_id, item.evidence)
        return tuple(collected.values())

    def warnings(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                f"event-cue candidate {refusal.surface!r} in {refusal.field} "
                f"[{refusal.char_start}:{refusal.char_end}] refused: {refusal.reason}"
                for refusal in self.refusals
            )
        )

    def describe(self) -> str:
        return (
            f"{len(self.matches)} cue(s) over {len(self.findings)} field(s), "
            f"{len(self.non_events)} non-event marker(s), {len(self.refusals)} refused"
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "findings": [finding.as_dict() for finding in self.findings],
        }


def _cue_match(
    prose: Prose,
    entry: Entry,
    *,
    rule: str,
    via: str,
    span: Span,
    words: Sequence[Word],
) -> CueMatch:
    cue: vocabulary.Cue = entry.row
    return CueMatch(
        field=prose.field,
        event_type=cue.event_type,
        strength=cue.strength,
        entry=entry.surface,
        surface=span.quote,
        rule=rule,
        via=via,
        char_start=span.char_start,
        char_end=span.char_end,
        sentence=words[0].sentence,
        words=tuple(word.index for word in words),
        note=cue.note,
    )


def _phrase_at(prose: Prose, start: int) -> Optional[tuple[CueMatch, int]]:
    """The longest multi-word cue beginning at this word, longest first so it wins its own span."""
    words = prose.words
    for length in PHRASE_LENGTHS:
        if start + length > len(words):
            continue
        window = words[start : start + length]
        if window[-1].sentence != words[start].sentence:
            continue
        if not mention_text.contiguous(words, start, start + length, source_text=prose.text):
            continue
        key = tuple(mention_text.fold(word.text) for word in window)
        entry = _single_claim(PHRASE_KEYS.get(key, ()))
        if entry is None:
            continue
        span = mention_text.quote_of(words, start, start + length, prose.text)
        return _cue_match(prose, entry, rule=RULE_PHRASE, via="phrase", span=span, words=window), length
    return None


def _word_at(prose: Prose, index: int) -> Optional[CueMatch]:
    word = prose.words[index]
    match = mention_text.read_word(word.text, CUE_LEXICON)
    if match is None:
        return None
    rule = {
        "as-printed": RULE_PRINTED,
        "latin-spelling": RULE_LATIN,
    }.get(match.rule, RULE_INFLECTED)
    return _cue_match(
        prose, match.entry, rule=rule, via=match.via, span=word.span, words=(word,)
    )


def _marker_at(prose: Prose, index: int) -> Optional[NonEventMatch]:
    word = prose.words[index]
    row = NON_EVENT_FOLDED.get(mention_text.fold(word.text)) or NON_EVENT_FOLDED.get(
        mention_text.fold(word.derived)
    )
    if row is None:
        return None
    return NonEventMatch(
        field=prose.field,
        entry=row.surface,
        surface=word.text,
        char_start=word.char_start,
        char_end=word.char_end,
        sentence=word.sentence,
        reason=row.reason,
    )


def _refusal(prose: Prose, word: Word, reason: str) -> Refusal:
    return Refusal(
        field=prose.field,
        surface=word.text,
        char_start=word.char_start,
        char_end=word.char_end,
        reason=reason,
    )


def _evidence(source: SourceField, match: CueMatch) -> Evidence:
    return source.evidence_at(
        match.span,
        method=METHOD,
        confidence=match.quality,
        notes=(
            f"{match.strength} event cue for {match.event_type.value}: {match.surface!r} read by "
            f"the {match.rule} rule against the vocabulary entry {match.entry!r} ({match.note})"
        ),
    )


def _marker_evidence(source: SourceField, marker: NonEventMatch) -> Evidence:
    return source.evidence_at(
        marker.span,
        method=METHOD,
        confidence=QUALITY[RULE_PRINTED],
        notes=f"non-event marker {marker.entry!r}: {marker.reason}",
    )


def find_cues(source: SourceField, prose: Prose) -> CueFinding:
    """Read one field once, left to right, letting a wider cue claim the words inside it."""
    if prose.is_empty:
        return CueFinding(field=prose.field)

    words = prose.words
    matches: list[CueMatch] = []
    refusals: list[Refusal] = []
    contested = set(CUE_LEXICON.contested)

    index = 0
    while index < len(words):
        phrase = _phrase_at(prose, index)
        if phrase is not None:
            match, length = phrase
            matches.append(match)
            index += length
            continue

        match = _word_at(prose, index)
        if match is not None:
            matches.append(match)
            index += 1
            continue

        folded = mention_text.fold(words[index].text)
        if folded in NON_CUE_FOLDED:
            refusals.append(
                _refusal(
                    prose,
                    words[index],
                    "names an event without naming which, so it decides nothing",
                )
            )
        elif folded in contested:
            refusals.append(
                _refusal(
                    prose,
                    words[index],
                    "the vocabulary claims two event types for it, so neither was asserted"
                )
            )
        index += 1

    markers = tuple(
        marker
        for position in range(len(words))
        if (marker := _marker_at(prose, position)) is not None
    )
    cited = tuple(
        replace(match, evidence=_evidence(source, match)) for match in matches
    )
    cited_markers = tuple(
        replace(marker, evidence=_marker_evidence(source, marker)) for marker in markers
    )
    return CueFinding(
        field=prose.field, matches=cited, non_events=cited_markers, refusals=tuple(refusals)
    )


def extract(readings: Sequence[tuple[SourceField, Prose]]) -> CueExtraction:
    """Every cue the record's own fields printed, with the spans that stated them."""
    if not readings:
        raise ValueError("cue extraction needs at least one field to read")
    record_id = readings[0][0].record_id
    findings = tuple(
        finding
        for finding in (find_cues(source, prose) for source, prose in readings)
        if finding.matches or finding.non_events or finding.refusals
    )
    return CueExtraction(record_id=record_id, findings=findings)


__all__ = [
    "CUE_LEXICON",
    "METHOD",
    "PROVIDER",
    "QUALITY",
    "RULE_INFLECTED",
    "RULE_LATIN",
    "RULE_PHRASE",
    "RULE_PRINTED",
    "CueExtraction",
    "CueFinding",
    "CueMatch",
    "NonEventMatch",
    "Refusal",
    "extract",
    "find_cues",
]
