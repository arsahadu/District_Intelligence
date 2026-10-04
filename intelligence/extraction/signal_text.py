"""Stage 8b word reading: stem and phrase rows, and the negation that refuses to believe them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from intelligence.config import operations_cues as vocabulary
from intelligence.extraction import mention_text
from intelligence.extraction.mention_text import Prose, Word
from intelligence.extraction.spans import Span
from intelligence.models.enums import ExtractionMethod

PROVIDER = "intelligence.extraction.signal_text"
METHOD = ExtractionMethod.DICTIONARY

RULE_PRINTED = "as-printed"
RULE_STEM = "stem"
RULE_PHRASE = "phrase"
RULE_LATIN = "latin-spelling"

#: How sure this reading is that the printed words are the wording the vocabulary named.
QUALITY = {
    RULE_PRINTED: 0.95,
    RULE_STEM: 0.85,
    RULE_PHRASE: 0.9,
    RULE_LATIN: 0.9,
}

#: A cue is only believed when its weight times this match quality clears the floor.
ACCEPTANCE_FLOOR = vocabulary.ACCEPTANCE_FLOOR

#: A number and the noun it counts are read as one phrase this far apart at most.
HEAD_WINDOW = 3


@dataclass(frozen=True)
class CompiledRow:
    """One surface from the vocabulary, split into the folded words it is matched by."""

    surface: str
    parts: tuple[str, ...]
    stems: tuple[bool, ...]
    rule: str
    row: Any
    via: str = "printed"

    @property
    def is_phrase(self) -> bool:
        return len(self.parts) > 1

    @property
    def specificity(self) -> int:
        return sum(len(part) for part in self.parts)

    @property
    def weight(self) -> float:
        return float(getattr(self.row, "weight", 1.0))

    @property
    def note(self) -> str:
        return str(getattr(self.row, "note", "") or "")


class SignalIndex:
    """One vocabulary family, keyed for left-to-right reading of a field's running text."""

    def __init__(self, name: str, rows: Sequence[Any]):
        self.name = name
        self.rows: tuple[CompiledRow, ...] = tuple(
            compiled for row in rows for compiled in compile_row(row)
        )
        self.exact: dict[str, list[CompiledRow]] = {}
        self.stems: list[CompiledRow] = []
        self.phrases: dict[tuple[str, ...], list[CompiledRow]] = {}
        for compiled in self.rows:
            if compiled.is_phrase:
                self.phrases.setdefault(compiled.parts, []).append(compiled)
            elif compiled.stems[0]:
                self.stems.append(compiled)
            else:
                self.exact.setdefault(compiled.parts[0], []).append(compiled)
        self.by_length: dict[int, tuple[tuple[tuple[str, ...], tuple[CompiledRow, ...]], ...]] = {}
        for length in {len(parts) for parts in self.phrases}:
            keys = tuple(
                sorted(
                    (
                        (parts, tuple(rows))
                        for parts, rows in self.phrases.items()
                        if len(parts) == length
                    ),
                    key=lambda item: (-sum(len(part) for part in item[0]), item[0]),
                )
            )
            self.by_length[length] = keys
        self.lengths = tuple(sorted(self.by_length, reverse=True))

    def for_word(self, folded: str) -> tuple[CompiledRow, ...]:
        """Every single-word row this printed word could be, most specific first."""
        hits = [*self.exact.get(folded, ())]
        hits += [row for row in self.stems if folded.startswith(row.parts[0])]
        return tuple(sorted(hits, key=lambda row: (-row.specificity, row.surface)))

    def phrase_at(
        self, words: Sequence[Word], start: int, *, source_text: str
    ) -> Optional[CompiledRow]:
        """The row these consecutive words spell, or None for none and for two readings."""
        for length in self.lengths:
            if start + length > len(words):
                continue
            window = words[start : start + length]
            if window[-1].sentence != words[start].sentence:
                continue
            if not mention_text.contiguous(words, start, start + length, source_text=source_text):
                continue
            folded = tuple(mention_text.fold(word.text) for word in window)
            for parts, rows in self.by_length[length]:
                if not _spells(parts, rows[0].stems, folded):
                    continue
                agreed = _agreed(rows)
                if agreed is not None:
                    return agreed
        return None

    def row_at(
        self, words: Sequence[Word], start: int, *, source_text: str
    ) -> Optional[tuple[CompiledRow, int]]:
        """The row that fits here and the number of words it takes, longest key first."""
        if start >= len(words):
            return None
        compiled = self.phrase_at(words, start, source_text=source_text)
        if compiled is not None:
            return compiled, len(compiled.parts)
        fitted = _most_specific(self.for_word(mention_text.fold(words[start].text)))
        return (fitted, 1) if fitted is not None else None


def _spells(parts: tuple[str, ...], stems: tuple[bool, ...], folded: tuple[str, ...]) -> bool:
    return all(
        folded[index] == part if not stems[index] else folded[index].startswith(part)
        for index, part in enumerate(parts)
    )


def compile_row(row: Any) -> tuple[CompiledRow, ...]:
    """Every spelling this surface answers to, a trailing ``*`` read as 'begins with'."""
    spellings = (row.surface, *getattr(row, "latin", ()))
    compiled: list[CompiledRow] = []
    for position, spelling in enumerate(spellings):
        parts = vocabulary.key_of(spelling)
        if not parts:
            continue
        stems = tuple(vocabulary.is_stem(part) for part in parts)
        folded = tuple(mention_text.fold(vocabulary.stem_of(part)) for part in parts)
        if len(parts) > 1:
            rule = RULE_PHRASE
        elif position:
            rule = RULE_LATIN
        else:
            rule = RULE_STEM if stems[0] else RULE_PRINTED
        compiled.append(
            CompiledRow(
                surface=spelling,
                parts=folded,
                stems=stems,
                rule=rule,
                row=row,
                via="latin" if position else "printed",
            )
        )
    return tuple(compiled)


def _agreed(rows: Sequence[CompiledRow]) -> Optional[CompiledRow]:
    if not rows:
        return None
    if len({row.row.claim for row in rows}) > 1:
        return None
    return rows[0]


def _most_specific(rows: Sequence[CompiledRow]) -> Optional[CompiledRow]:
    """The longest row that fits this word, unless two of equal length claim it differently."""
    if not rows:
        return None
    top = rows[0].specificity
    return _agreed([row for row in rows if row.specificity == top])


@dataclass(frozen=True)
class RowMatch:
    """One stretch of one field that read as a vocabulary row, with the span that proves it."""

    field: str
    entry: str
    surface: str
    rule: str
    via: str
    char_start: int
    char_end: int
    sentence: int
    words: tuple[int, ...]
    note: str
    row: Any = None

    @property
    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    @property
    def quality(self) -> float:
        return QUALITY[self.rule]

    @property
    def strength(self) -> float:
        return float(getattr(self.row, "weight", 1.0)) * self.quality

    @property
    def accepted(self) -> bool:
        return self.strength >= ACCEPTANCE_FLOOR - 1e-9

    def describe(self) -> str:
        return f"{self.entry!r} in {self.field} [{self.char_start}:{self.char_end}] ({self.rule})"

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "entry": self.entry,
            "surface": self.surface,
            "rule": self.rule,
            "via": self.via,
            "quality": self.quality,
            "strength": round(self.strength, 4),
            "accepted": self.accepted,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "sentence": self.sentence,
            "words": list(self.words),
            "note": self.note,
        }


@dataclass(frozen=True)
class Refusal:
    """A word or phrase that looked like a row and was not believed."""

    field: str
    surface: str
    char_start: int
    char_end: int
    sentence: int
    reason: str

    @property
    def span(self) -> Span:
        return Span(quote=self.surface, char_start=self.char_start, char_end=self.char_end)

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "reason": self.reason,
        }


def negated_in(prose: Prose, taken: Sequence[int]) -> Optional[str]:
    """The word beside this match that says the opposite, if one stands in the same sentence."""
    words = prose.words
    matched = set(taken)
    sentence = words[taken[0]].sentence
    first, last = min(taken), max(taken)
    from_ = max(0, first - vocabulary.NEGATION_WINDOW)
    until = min(len(words), last + 1 + vocabulary.NEGATION_WINDOW)
    for index in range(from_, until):
        if index in matched or words[index].sentence != sentence:
            continue
        if _is_negation(words[index].text):
            return words[index].text
    return None


def _is_negation(surface: str) -> bool:
    folded = mention_text.fold(surface)
    if not folded:
        return False
    if any(marker in folded for marker in vocabulary.NEGATION_SUBSTRINGS):
        return True
    return folded in vocabulary.NEGATION_WORDS or folded.endswith("n't")


def find_matches(
    prose: Prose, index: SignalIndex
) -> tuple[tuple[RowMatch, ...], tuple[Refusal, ...]]:
    """Read one field left to right, letting a longer row claim the words inside it."""
    if prose.is_empty:
        return (), ()

    words = prose.words
    matches: list[RowMatch] = []
    refusals: list[Refusal] = []

    def refusal(from_: int, until: int, surface: str, reason: str) -> Refusal:
        return Refusal(
            field=prose.field,
            surface=surface,
            char_start=words[from_].char_start,
            char_end=words[until - 1].char_end,
            sentence=words[from_].sentence,
            reason=reason,
        )

    position = 0
    while position < len(words):
        compiled = index.phrase_at(words, position, source_text=prose.text)
        if compiled is not None:
            length = len(compiled.parts)
            span = mention_text.quote_of(words, position, position + length, prose.text)
            taken = range(position, position + length)
            denial = negated_in(prose, taken)
            if denial is None:
                matches.append(
                    RowMatch(
                        field=prose.field,
                        entry=compiled.surface,
                        surface=span.quote,
                        rule=RULE_PHRASE,
                        via=compiled.via,
                        char_start=span.char_start,
                        char_end=span.char_end,
                        sentence=words[position].sentence,
                        words=tuple(word.index for word in words[position : position + length]),
                        note=compiled.note,
                        row=compiled.row,
                    )
                )
            else:
                refusals.append(
                    refusal(position, position + length, span.quote, f"the sentence says {denial!r}")
                )
            position += length
            continue

        word = words[position]
        rows = index.for_word(mention_text.fold(word.text))
        if not rows:
            position += 1
            continue
        fitted = _most_specific(rows)
        if fitted is None:
            refusals.append(
                refusal(
                    position,
                    position + 1,
                    word.text,
                    f"rows of {index.name} of one length claim it differently",
                )
            )
        else:
            denial = negated_in(prose, (position,))
            if denial is None:
                matches.append(
                    RowMatch(
                        field=prose.field,
                        entry=fitted.surface,
                        surface=word.text,
                        rule=fitted.rule,
                        via=fitted.via,
                        char_start=word.char_start,
                        char_end=word.char_end,
                        sentence=word.sentence,
                        words=(position,),
                        note=fitted.note,
                        row=fitted.row,
                    )
                )
            else:
                refusals.append(
                    refusal(position, position + 1, word.text, f"the sentence says {denial!r}")
                )
        position += 1

    return tuple(matches), tuple(refusals)


__all__ = [
    "ACCEPTANCE_FLOOR",
    "HEAD_WINDOW",
    "METHOD",
    "PROVIDER",
    "QUALITY",
    "RULE_LATIN",
    "RULE_PHRASE",
    "RULE_PRINTED",
    "RULE_STEM",
    "CompiledRow",
    "Refusal",
    "RowMatch",
    "SignalIndex",
    "compile_row",
    "find_matches",
    "negated_in",
]
