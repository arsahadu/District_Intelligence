"""Word-level reading of a stamp-free body; what a word means is decided by places.py and actors.py."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Optional, Sequence

from intelligence.config import mention_words as vocabulary
from intelligence.extraction import morphology
from intelligence.extraction.boilerplate import BodySplit, drop_ranges
from intelligence.extraction.normalize import MappedText
from intelligence.extraction.spans import SourceField, Span

PROVIDER = "intelligence.extraction.mention_text"

#: ம் and the pulli are how a stem is written down, not part of its identity.
FINAL_AM = morphology.FINAL_AM
PULLI = morphology.PULLI
MIN_STEM = morphology.MIN_STEM_LENGTH
MAX_PEEL_DEPTH = 2
MAX_STEM_VARIANTS = 12

TERMINAL_RUN = re.compile(r"[.!?।|]+")
GAP_ONLY_SPACE = re.compile(r" *\n? *")


class MentionTextError(ValueError):
    """A word could not be read as printed."""


def fold(surface: str) -> str:
    """The stem key: a final ம் or pulli is spelling; a looser fold invented வீதி from விதி."""
    text = unicodedata.normalize("NFC", surface).strip().casefold()
    if text.endswith(FINAL_AM):
        return text[: -len(FINAL_AM)]
    if text.endswith(PULLI):
        return text[: -len(PULLI)]
    return text


_NEVER_PLACE: frozenset[str] = frozenset(
    word
    for group in (
        vocabulary.NON_PLACE_WORDS,
        vocabulary.NON_NAME_TOKENS,
        vocabulary.CALENDAR_TOKENS,
        vocabulary.LATIN_STOP_WORDS,
        vocabulary.LOCATIONAL_POSTPOSITIONS,
    )
    for word in group
)
_NEVER_ENTITY: frozenset[str] = frozenset(
    word
    for group in (
        vocabulary.NON_NAME_TOKENS,
        vocabulary.CALENDAR_TOKENS,
        vocabulary.LATIN_STOP_WORDS,
        vocabulary.LOCATIONAL_POSTPOSITIONS,
    )
    for word in group
)
_NEVER_PLACE_FOLDED: frozenset[str] = frozenset(fold(word) for word in _NEVER_PLACE)
_NEVER_ENTITY_FOLDED: frozenset[str] = frozenset(fold(word) for word in _NEVER_ENTITY)
_LOCATIONAL_FOLDED: frozenset[str] = frozenset(
    fold(word) for word in vocabulary.LOCATIONAL_POSTPOSITIONS
)
_DIRECTION_FOLDED: frozenset[str] = frozenset(
    fold(word) for word in vocabulary.DIRECTION_QUALIFIERS
)


def _in_lists(surface: str, words: frozenset[str], folded: frozenset[str]) -> bool:
    text = surface.strip()
    if not text:
        return True
    return text.casefold() in words or fold(text) in folded


def blocked(surface: str) -> bool:
    """A word no place mention may be built from, matched as printed and as a stem."""
    return _in_lists(surface, _NEVER_PLACE, _NEVER_PLACE_FOLDED)


def not_an_entity(surface: str) -> bool:
    """A word no actor or name may be built from; NON_PLACE_WORDS is about places only."""
    return _in_lists(surface, _NEVER_ENTITY, _NEVER_ENTITY_FOLDED)


def locational_postposition(surface: str) -> bool:
    return fold(surface) in _LOCATIONAL_FOLDED


def direction_word(surface: str) -> bool:
    return fold(surface) in _DIRECTION_FOLDED


@dataclass(frozen=True)
class Entry:

    surface: str
    lexicon: str
    kind: str
    claim: tuple[str, ...]
    latin: tuple[str, ...]
    note: str
    row: object

    @property
    def is_type_word(self) -> bool:
        return self.kind.endswith("type-word")


@dataclass(frozen=True)
class Candidate:

    form: str
    label: str
    rule: str

    @property
    def is_printed(self) -> bool:
        return self.rule == "as-printed"

    @property
    def is_plural(self) -> bool:
        return "plural" in self.label

    @property
    def is_locative(self) -> bool:
        return self.label in vocabulary.LOCATIVE_LABELS


@dataclass(frozen=True)
class Match:

    surface: str
    entry: Entry
    stem: str
    label: str
    rule: str
    via: str

    @property
    def is_plural(self) -> bool:
        return "plural" in self.label

    @property
    def is_locative(self) -> bool:
        return self.label in vocabulary.LOCATIVE_LABELS

    @property
    def inflected(self) -> bool:
        return self.rule != "as-printed"

    def as_dict(self) -> dict:
        return {
            "surface": self.surface,
            "entry": self.entry.surface,
            "lexicon": self.entry.lexicon,
            "stem": self.stem,
            "label": self.label,
            "rule": self.rule,
            "via": self.via,
        }


def _rows(name: str, rows: Iterable, kind: str, claim) -> tuple[Entry, ...]:
    return tuple(
        Entry(
            surface=row.surface,
            lexicon=name,
            kind=kind,
            claim=claim(row),
            latin=tuple(row.latin),
            note=row.note,
            row=row,
        )
        for row in rows
    )


class Lexicon:
    """Fold and Latin keys over one family of lists, a contested key refused rather than guessed."""

    def __init__(
        self,
        name: str,
        groups: Sequence[tuple[str, Sequence[Entry]]],
        rejects: Callable[[str], bool],
    ):
        self.name = name
        self.rejects = rejects
        self.entries: tuple[Entry, ...] = tuple(row for _, rows in groups for row in rows)
        self._fold: dict[str, list[Entry]] = {}
        self._latin: dict[str, list[Entry]] = {}
        for entry in self.entries:
            self._fold.setdefault(fold(entry.surface), []).append(entry)
            for latin in entry.latin:
                self._latin.setdefault(latin.strip().casefold(), []).append(entry)

    @property
    def contested(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                key
                for keys in (self._fold, self._latin)
                for key, rows in keys.items()
                if len({row.claim for row in rows}) > 1
            )
        )

    @staticmethod
    def _agreed(rows: Sequence[Entry]) -> Optional[Entry]:
        if not rows:
            return None
        if len({row.claim for row in rows}) > 1:
            return None
        return rows[0]

    def lookup(self, surface: str, candidates: Sequence[Candidate]) -> Optional[Match]:
        """The one row these readings point at, or None when they point at none or at two."""
        for candidate in candidates:
            if len(candidate.form) < MIN_STEM:
                continue
            hit = self._agreed(self._fold.get(fold(candidate.form), ()))
            if hit is not None:
                return Match(
                    surface=surface,
                    entry=hit,
                    stem=candidate.form,
                    label=candidate.label,
                    rule=candidate.rule,
                    via="fold",
                )
        return None

    def lookup_latin(self, surface: str) -> Optional[Match]:
        """The row whose stated Latin spelling this word is, Tamil script and all."""
        hit = self._agreed(self._latin.get(surface.strip().casefold(), ()))
        if hit is None:
            return None
        return Match(
            surface=surface, entry=hit, stem=surface.strip(), label="none", rule="latin-spelling", via="latin"
        )

    def match(self, surface: str) -> Optional[Match]:
        return self.lookup(surface, stem_candidates(surface))


def _place_claim(row) -> tuple[str, ...]:
    return (row.mention_type.value, row.granularity.value)


def _actor_claim(row) -> tuple[str, ...]:
    return (row.actor_type.value,)


PLACE_LEXICON = Lexicon(
    "place",
    (
        (
            "PLACE_TYPES",
            _rows("PLACE_TYPES", vocabulary.PLACE_TYPES, "type-word", _place_claim),
        ),
        (
            "PLACE_NAMES",
            _rows("PLACE_NAMES", vocabulary.PLACE_NAMES, "known-name", _place_claim),
        ),
        (
            "LATIN_PLACE_WORDS",
            _rows("LATIN_PLACE_WORDS", vocabulary.LATIN_PLACE_WORDS, "latin-type-word", _place_claim),
        ),
    ),
    blocked,
)

ACTOR_LEXICON = Lexicon(
    "actor",
    (
        ("ACTOR_HEADS", _rows("ACTOR_HEADS", vocabulary.ACTOR_HEADS, "body-head", _actor_claim)),
        (
            "COLLECTIVE_HEADS",
            _rows("COLLECTIVE_HEADS", vocabulary.COLLECTIVE_HEADS, "collective-head", _actor_claim),
        ),
        (
            "OFFICIAL_TITLES",
            _rows("OFFICIAL_TITLES", vocabulary.OFFICIAL_TITLES, "office-title", _actor_claim),
        ),
    ),
    not_an_entity,
)


    #: A stem-final ம் hardens to ங் before a plural tail, so மாநிலங்கள் reads back through மாநிலங்.
HARDENED_AM = "ங்"


def _peel_once(seed: Candidate) -> tuple[Candidate, ...]:
    """One reading deeper than this one: a tail, the vowel a tail swallowed, or a qualifier."""
    text = seed.form
    peeled: list[Candidate] = []
    for tail in vocabulary.EXTRA_TAILS:
        if not text.endswith(tail):
            continue
        form = text[: -len(tail)]
        if len(form) < MIN_STEM:
            continue
        peeled.append(Candidate(form, vocabulary.TAIL_LABELS[tail], "strip-tail"))
    if text and text[-1] in morphology.VOWEL_SIGNS and len(text) - 1 >= MIN_STEM:
        peeled.append(Candidate(text[:-1], seed.label, "restore-vowel"))
    if text.endswith(HARDENED_AM) and len(text) - len(HARDENED_AM) >= MIN_STEM:
        peeled.append(Candidate(text[: -len(HARDENED_AM)], seed.label, "restore-hardened-am"))
    residual = _unqualified(text)
    if residual is not None:
        peeled.append(Candidate(residual, seed.label, "split-qualifier"))
    return tuple(peeled)


def _unqualified(form: str) -> Optional[str]:
    """The stem behind a direction prefix: தென்மாவட்ட leaves மாவட்ட standing alone."""
    for cut in range(MIN_STEM, len(form) - MIN_STEM + 1):
        if fold(form[:cut]) not in _DIRECTION_FOLDED:
            continue
        start = cut
        while start < len(form) and unicodedata.category(form[start])[0] == "M":
            start += 1
        if len(form) - start >= MIN_STEM:
            return form[start:]
    return None



def stem_candidates(word: str) -> tuple[Candidate, ...]:
    """Every reading of this word, printed form first so it borrows no case label."""
    text = word.strip()
    if not text:
        raise MentionTextError("an empty word has no stem")

    printed = Candidate(text, "none", "as-printed")
    ordered: list[Candidate] = [printed]
    seen = {text}

    def push(candidate: Candidate) -> bool:
        if len(candidate.form) < MIN_STEM or candidate.form in seen:
            return False
        seen.add(candidate.form)
        ordered.append(candidate)
        return len(ordered) < MAX_STEM_VARIANTS

    frontier = [printed]
    for suffix in morphology.suffix_candidates(text):
        if suffix.is_bare_stem:
            continue
        candidate = Candidate(suffix.stem, suffix.label, suffix.rule)
        if push(candidate):
            frontier.append(candidate)

    for _ in range(MAX_PEEL_DEPTH):
        following: list[Candidate] = []
        for seed in frontier:
            for candidate in _peel_once(seed):
                if push(candidate):
                    following.append(candidate)
        if not following or len(ordered) >= MAX_STEM_VARIANTS:
            break
        frontier = following
    return tuple(ordered)


def read_word(surface: str, lexicon: Lexicon) -> Optional[Match]:
    """The row this word points at, or None for none or for two; a refused peel condemns no word."""
    candidates = [
        candidate for candidate in stem_candidates(surface) if not lexicon.rejects(candidate.form)
    ]
    match = lexicon.lookup(surface, candidates)
    if match is not None:
        return match
    return None if lexicon.rejects(surface) else lexicon.lookup_latin(surface)



@dataclass(frozen=True)
class Word:

    text: str
    derived: str
    char_start: int
    char_end: int
    derived_start: int
    derived_end: int
    index: int
    sentence: int

    @property
    def span(self) -> Span:
        return Span(quote=self.text, char_start=self.char_start, char_end=self.char_end)

    @property
    def is_number(self) -> bool:
        return self.derived.isdigit()

    @property
    def is_latin(self) -> bool:
        return self.text.isascii()

    @property
    def capitalised(self) -> bool:
        return bool(self.text) and self.text[0].isupper()

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return stem_candidates(self.text)

    @property
    def blocked(self) -> bool:
        return any(blocked(candidate.form) for candidate in self.candidates)

    @property
    def refused_as_entity(self) -> bool:
        return any(not_an_entity(candidate.form) for candidate in self.candidates)

    @property
    def locative(self) -> bool:
        return any(candidate.is_locative for candidate in self.candidates)

    @property
    def locational(self) -> bool:
        return locational_postposition(self.text)

    @property
    def direction(self) -> bool:
        return direction_word(self.text)

    def entry(self, lexicon: Lexicon) -> Optional[Match]:
        return read_word(self.text, lexicon)

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "sentence": self.sentence,
        }


@dataclass(frozen=True)
class Prose:
    """The running text of one field, tokenised and still traceable to the untouched source."""

    field: str
    text: str
    words: tuple[Word, ...]
    sentences: tuple[tuple[int, int], ...]
    dropped: tuple[tuple[int, int], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.words

    def sentence_bounds(self, index: int) -> tuple[int, int]:
        start, end = self.sentences[index]
        return self.words[start].char_start, self.words[end - 1].char_end

    def sentence_words(self, index: int) -> tuple[Word, ...]:
        start, end = self.sentences[index]
        return self.words[start:end]

    def context_window(
        self, index: int, *, limit: int = vocabulary.CONTEXT_WINDOW_CHARACTERS
    ) -> str:
        start, end = self.sentences[self.words[index].sentence]
        left = self.words[start].char_start
        right = self.words[end - 1].char_end
        window = self.text[left:right]
        if len(window) <= limit:
            return window
        word = self.words[index]
        centre = (word.char_start + word.char_end) // 2
        from_ = max(left, centre - limit // 2)
        return self.text[from_ : min(right, from_ + limit)]

    def as_dicts(self) -> list[dict]:
        return [word.as_dict() for word in self.words]


def _sentence_breaks(mapped: MappedText) -> list[int]:
    """Sentence ends, where an initial such as "K. Rajendran" is not one."""
    breaks: list[int] = []
    text = mapped.text
    for match in TERMINAL_RUN.finditer(text):
        after = match.end()
        if after < len(text) and text[after] not in " \t\n":
            continue
        before = text[: match.start()].rstrip()
        if not before:
            continue
        if len(before) >= 2 and before[-1].isascii() and before[-1].isalpha() and not before[-2].isalnum():
            continue
        breaks.append(after)
    return breaks


def build_prose(
    source: SourceField,
    mapped: MappedText,
    *,
    split: Optional[BodySplit] = None,
    skip_numbers: bool = True,
) -> Prose:
    """Tokenise one field's running text; nothing before the last dropped run is prose."""
    dropped = tuple(drop_ranges(source.text, split.items)) if split is not None else ()
    gate = max((end for _, end in dropped), default=0)

    breaks = _sentence_breaks(mapped)
    boundaries = [0, *breaks, len(mapped.text)]

    words: list[Word] = []
    for sentence, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        for token in morphology.tokenize(mapped.text[start:end]):
            span = mapped.project(start + token.char_start, start + token.char_end)
            if span.char_start < gate:
                continue
            if skip_numbers and token.quote.isdigit():
                continue
            words.append(
                Word(
                    text=span.quote,
                    derived=mapped.text[
                        start + token.char_start : start + token.char_end
                    ].strip(),
                    char_start=span.char_start,
                    char_end=span.char_end,
                    derived_start=start + token.char_start,
                    derived_end=start + token.char_end,
                    index=len(words),
                    sentence=sentence,
                )
            )

    grouped: list[Word] = []
    ranges: list[tuple[int, int]] = []
    current: Optional[int] = None
    start = 0
    for word in words:
        if current is None:
            current = word.sentence
        if word.sentence != current:
            ranges.append((start, len(grouped)))
            start = len(grouped)
            current = word.sentence
        grouped.append(replace(word, sentence=len(ranges)))
    if grouped:
        ranges.append((start, len(grouped)))

    return Prose(
        field=source.field,
        text=source.text,
        words=tuple(grouped),
        sentences=tuple(ranges),
        dropped=dropped,
    )


def quote_of(words: Sequence[Word], start: int, end: int, source_text: str) -> Span:
    first, last = words[start], words[end - 1]
    quote = source_text[first.char_start : last.char_end]
    if not quote.strip():
        raise MentionTextError(f"words {start}:{end} quote nothing from the field")
    return Span(quote=quote, char_start=first.char_start, char_end=last.char_end)


def contiguous(words: Sequence[Word], start: int, end: int, *, source_text: str) -> bool:
    """True when only spaces separate these words in the field itself."""
    for index in range(start, end - 1):
        gap = source_text[words[index].char_end : words[index + 1].char_start]
        if not GAP_ONLY_SPACE.fullmatch(gap):
            return False
    return True


def overlaps_dropped(prose: Prose, char_start: int, char_end: int) -> bool:
    return any(char_start < end and start < char_end for start, end in prose.dropped)


def phrase(
    prose: Prose,
    head: int,
    *,
    max_modifiers: int = vocabulary.MAX_MODIFIERS,
    accept: Optional[callable] = None,
) -> tuple[int, ...]:
    """The word indices a head shares a phrase with its preceding modifiers, head last."""
    words = prose.words
    run = [head]
    index = head - 1
    while index >= 0 and len(run) - 1 < max_modifiers:
        candidate = words[index]
        if candidate.sentence != words[head].sentence:
            break
        if not contiguous(words, index, head + 1, source_text=prose.text):
            break
        if accept is not None and not accept(candidate):
            break
        run.append(index)
        index -= 1
    run.sort()
    return tuple(run)
