"""Place expressions: which words in a field name a place, and which ones came close."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from intelligence.config import mention_words as vocabulary
from intelligence.extraction import mention_text
from intelligence.extraction.mention_text import Match, Prose, Word
from intelligence.extraction.spans import Span
from intelligence.models.enums import GranularityLevel, MentionType

DATELINE = "dateline"
KNOWN_NAME = "known_name"
TYPE_HEAD = "type_head"
CASE_MARKED = "case_marked"
LATIN_NAME = "latin_name"

KINDS = frozenset({DATELINE, KNOWN_NAME, TYPE_HEAD, CASE_MARKED, LATIN_NAME})

GENERIC_UNLOCATED = "generic_unlocated"
PLURAL_GENERIC = "plural_generic"
BLOCKED_OBLIQUE = "blocked_oblique"

REASONS = frozenset({GENERIC_UNLOCATED, PLURAL_GENERIC, BLOCKED_OBLIQUE})

CONFIDENCE_DATELINE = 0.9
CONFIDENCE_CASED_NAME = 0.85
CONFIDENCE_NAME = 0.8
CONFIDENCE_HEAD = 0.75
CONFIDENCE_CASED_TYPE = 0.6
#: A route or proximity word straight after a name corroborates it, so its confidence rises.
CONFIDENCE_BOOST = 0.05
MAX_CONFIDENCE = 0.95

#: Granularities broad enough to hold an event, used only when nothing names a location.
CONTAINER_LEVELS: frozenset[GranularityLevel] = frozenset(
    {
        GranularityLevel.COUNTRY,
        GranularityLevel.STATE,
        GranularityLevel.DISTRICT,
        GranularityLevel.SUB_DIVISION,
        GranularityLevel.TALUK,
        GranularityLevel.TOWN,
    }
)

MAX_LATIN_NAME_WORDS = 4


@dataclass(frozen=True)
class PlaceExpression:
    """One place the text states, quoted in the field's own coordinates."""

    kind: str
    field: str
    surface: str
    span: Span
    words: tuple[int, ...]
    entry: str
    lexicon: str
    mention_type: MentionType
    granularity: GranularityLevel
    stem_folds: tuple[str, ...]
    names: tuple[str, ...]
    label: str
    rule: str
    via: str
    note: str
    confidence: float
    context: str
    sentence: int
    postposition: Optional[str] = None

    @property
    def char_start(self) -> int:
        return self.span.char_start

    @property
    def char_end(self) -> int:
        return self.span.char_end

    @property
    def coalesce_key(self) -> tuple:
        return (self.kind, self.stem_folds, self.mention_type, self.granularity)

    def describe(self) -> str:
        return (
            f"{self.kind} {self.surface!r} as {self.entry} "
            f"({self.mention_type.value}/{self.granularity.value}) from {self.field}"
        )

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "field": self.field,
            "surface": self.surface,
            "char_start": self.span.char_start,
            "char_end": self.span.char_end,
            "entry": self.entry,
            "lexicon": self.lexicon,
            "mention_type": self.mention_type.value,
            "granularity": self.granularity.value,
            "names": list(self.names),
            "label": self.label,
            "rule": self.rule,
            "via": self.via,
            "postposition": self.postposition,
            "confidence": self.confidence,
            "note": self.note,
        }


@dataclass(frozen=True)
class Rejection:
    """A word that looked like a place and was refused, with the reason kept."""

    reason: str
    field: str
    surface: str
    char_start: int
    char_end: int
    entry: str
    label: str
    note: str

    def describe(self) -> str:
        return f"{self.surface!r} ({self.entry}) in {self.field} refused: {self.reason}"

    def as_dict(self) -> dict:
        return {
            "reason": self.reason,
            "field": self.field,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "entry": self.entry,
            "label": self.label,
            "note": self.note,
        }


@dataclass(frozen=True)
class PlaceFinding:
    """What one field said about places: the claims and the near misses."""

    field: str
    expressions: tuple[PlaceExpression, ...] = ()
    rejections: tuple[Rejection, ...] = ()

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "expressions": [expression.as_dict() for expression in self.expressions],
            "rejections": [rejection.as_dict() for rejection in self.rejections],
        }


class PlaceReadingError(ValueError):
    """A field could not be read for places."""


def _matched(word: Word) -> Optional[Match]:
    return word.entry(mention_text.PLACE_LEXICON)


def _stem_of(word: Word) -> str:
    match = _matched(word)
    return word.text if match is None else match.stem


def _name_of(word: Word) -> Optional[str]:
    """The lexicon name this word is, when the text is naming rather than describing."""
    match = _matched(word)
    if match is None or match.is_plural or match.entry.kind != "known-name":
        return None
    return match.entry.surface


def _accepts(word: Word) -> bool:
    """Whether a phrase may run back through this word to reach its head."""
    if word.blocked:
        return False
    if word.direction:
        return True
    if mention_text.fold(word.text) in _QUALIFIER_FOLDED:
        return True
    return _name_of(word) is not None


_QUALIFIER_FOLDED = frozenset(mention_text.fold(word) for word in vocabulary.PLACE_QUALIFIERS)


def _phrase(prose: Prose, head: int) -> tuple[int, ...]:
    """The head and the elements in front of it, head last."""
    words = prose.words
    taken = [head]
    index = head - 1
    while index >= 0 and len(taken) - 1 < vocabulary.MAX_MODIFIERS:
        candidate = words[index]
        if candidate.sentence != words[head].sentence:
            break
        if not mention_text.contiguous(words, index, head + 1, source_text=prose.text):
            break
        if mention_text.overlaps_dropped(prose, candidate.char_start, words[head].char_end):
            break
        if not _accepts(candidate):
            break
        taken.append(index)
        index -= 1
    taken.sort()
    return tuple(taken)


def _opens_dateline(prose: Prose, word: Word) -> bool:
    """The printed separator a dateline puts between a place and the story."""
    return prose.text[word.char_end : word.char_end + 1] == ":"


def _after_postposition(prose: Prose, index: int) -> Optional[str]:
    words = prose.words
    if index + 1 >= len(words):
        return None
    following = words[index + 1]
    if following.sentence != words[index].sentence:
        return None
    if not mention_text.contiguous(words, index, index + 2, source_text=prose.text):
        return None
    return following.text if following.locational else None


def _rejection(prose: Prose, word: Word, reason: str, entry: str, label: str, note: str) -> Rejection:
    return Rejection(
        reason=reason,
        field=prose.field,
        surface=word.text,
        char_start=word.char_start,
        char_end=word.char_end,
        entry=entry,
        label=label,
        note=note,
    )


def _expression(
    prose: Prose,
    kind: str,
    index: int,
    match: Match,
    run: Sequence[int],
    *,
    note: str,
    confidence: float,
) -> PlaceExpression:
    row = match.entry.row
    words = prose.words
    span = mention_text.quote_of(words, run[0], run[-1] + 1, prose.text)
    postposition = _after_postposition(prose, run[-1])
    if postposition is not None:
        note = f"{note}; the text puts {postposition!r} straight after it"
        confidence = min(MAX_CONFIDENCE, confidence + CONFIDENCE_BOOST)
    return PlaceExpression(
        kind=kind,
        field=prose.field,
        surface=span.quote,
        span=span,
        words=tuple(run),
        entry=match.entry.surface,
        lexicon=match.entry.lexicon,
        mention_type=row.mention_type,
        granularity=row.granularity,
        stem_folds=tuple(mention_text.fold(_stem_of(words[i])) for i in run),
        names=tuple(
            sorted({name for i in run for name in (_name_of(words[i]),) if name})
        ),
        label=match.label,
        rule=match.rule,
        via=match.via,
        note=note,
        confidence=confidence,
        context=prose.context_window(index),
        sentence=words[index].sentence,
        postposition=postposition,
    )


def _latin_names(prose: Prose, claimed: Sequence[Span]) -> tuple[PlaceExpression, ...]:
    """Multi-word Latin spellings the lexicon states, which tokenising breaks in two."""
    found: list[PlaceExpression] = []
    words = prose.words
    taken = list(claimed)
    for start in range(len(words)):
        if not words[start].is_latin or words[start].blocked:
            continue
        for width in range(2, MAX_LATIN_NAME_WORDS + 1):
            run = list(range(start, start + width))
            if run[-1] >= len(words):
                break
            if not mention_text.contiguous(words, run[0], run[-1] + 1, source_text=prose.text):
                break
            if words[run[-1]].blocked:
                break
            surface = " ".join(words[i].text for i in run)
            match = mention_text.PLACE_LEXICON.lookup_latin(surface)
            if match is None:
                continue
            span = mention_text.quote_of(words, run[0], run[-1] + 1, prose.text)
            if _overlaps(span, taken):
                continue
            taken.append(span)
            found.append(
                _expression(
                    prose,
                    LATIN_NAME,
                    start,
                    match,
                    run,
                    note=(
                        f"{surface!r} is how {match.entry.surface!r} is spelled in Latin script, "
                        "read as the words the text printed"
                    ),
                    confidence=CONFIDENCE_NAME,
                )
            )
    return tuple(found)


def _overlaps(span: Span, spans: Sequence[Span]) -> bool:
    return any(
        span.char_start < other.char_end and other.char_start < span.char_end for other in spans
    )


def _covers(wide: Span, narrow: Span) -> bool:
    return wide.char_start <= narrow.char_start and narrow.char_end <= wide.char_end


def find(prose: Prose) -> PlaceFinding:
    if prose.is_empty:
        return PlaceFinding(field=prose.field)

    expressions: list[PlaceExpression] = []
    rejections: list[Rejection] = []
    words = prose.words

    for index, word in enumerate(words):
        match = _matched(word)
        if match is None:
            if word.blocked and word.locative:
                rejections.append(
                    _rejection(
                        prose,
                        word,
                        BLOCKED_OBLIQUE,
                        "",
                        "",
                        note=(
                            f"{word.text!r} is in a case that can hold a place, but the lexicon "
                            "lists it as a word that never names one"
                        ),
                    )
                )
            continue

        if index == 0 and _opens_dateline(prose, word) and match.entry.kind == "known-name":
            expressions.append(
                _expression(
                    prose,
                    DATELINE,
                    index,
                    match,
                    (index,),
                    note=(
                        f"{word.text!r} opens the body with a colon after it, which is how this "
                        "feed datelines the place a report comes from"
                    ),
                    confidence=CONFIDENCE_DATELINE,
                )
            )
            continue

        if match.entry.kind == "known-name":
            expressions.append(
                _expression(
                    prose,
                    KNOWN_NAME,
                    index,
                    match,
                    (index,),
                    note=(
                        f"{word.text!r} is listed as the name {match.entry.surface!r}"
                        + (
                            f", read through its {match.label} form"
                            if match.rule != "as-printed"
                            else ""
                        )
                    ),
                    confidence=CONFIDENCE_CASED_NAME if match.is_locative else CONFIDENCE_NAME,
                )
            )
            continue

        if match.is_plural:
            rejections.append(
                _rejection(
                    prose,
                    word,
                    PLURAL_GENERIC,
                    match.entry.surface,
                    match.label,
                    note=(
                        f"{word.text!r} is the {match.label} of the kind word "
                        f"{match.entry.surface!r}: it says what sort of place, not which one"
                    ),
                )
            )
            continue

        run = _phrase(prose, index)
        named = [i for i in run[:-1] if _name_of(words[i]) is not None]
        if named:
            expressions.append(
                _expression(
                    prose,
                    TYPE_HEAD,
                    index,
                    match,
                    run,
                    note=(
                        f"{word.text!r} is a kind word the text has named: "
                        + " ".join(words[i].text for i in run)
                    ),
                    confidence=CONFIDENCE_HEAD,
                )
            )
            continue
        if match.rule == "split-qualifier":
            expressions.append(
                _expression(
                    prose,
                    TYPE_HEAD,
                    index,
                    match,
                    run,
                    note=(
                        f"{word.text!r} says which one itself: the qualifier is part of the word, "
                        f"so the text names a kind of {match.entry.surface!r}"
                    ),
                    confidence=CONFIDENCE_HEAD,
                )
            )
            continue
        if match.is_locative:
            expressions.append(
                _expression(
                    prose,
                    CASE_MARKED,
                    index,
                    match,
                    run,
                    note=(
                        f"{word.text!r} carries the {match.label} case, so the text places the "
                        f"event at a {match.entry.surface!r} without naming which one"
                    ),
                    confidence=CONFIDENCE_CASED_TYPE,
                )
            )
            continue
        rejections.append(
            _rejection(
                prose,
                word,
                GENERIC_UNLOCATED,
                match.entry.surface,
                match.label,
                note=(
                    f"{word.text!r} is the kind word {match.entry.surface!r} with no name in "
                    "front of it and no case on it: the text never located this one"
                ),
            )
        )

    found = _latin_names(prose, [expression.span for expression in expressions])
    expressions.extend(found)

    heads = [
        expression.span
        for expression in expressions
        if expression.kind in (TYPE_HEAD, CASE_MARKED)
    ]
    kept = tuple(
        expression
        for expression in expressions
        if expression.kind not in (KNOWN_NAME, LATIN_NAME)
        or not any(_covers(head, expression.span) for head in heads)
    )
    return PlaceFinding(field=prose.field, expressions=kept, rejections=tuple(rejections))
