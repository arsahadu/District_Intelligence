"""Actor mentions: the parties the text ties to what happened, each citing its phrase and its cue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Optional, Sequence

from intelligence.config import mention_words as vocabulary
from intelligence.extraction import language, mention_text, morphology, transliteration
from intelligence.extraction.mention_text import ACTOR_LEXICON, PLACE_LEXICON, Prose, Word
from intelligence.extraction.normalize import normalize as normalize_text
from intelligence.extraction.spans import SourceField, Span
from intelligence.models.actors import Actor
from intelligence.models.evidence import Evidence
from intelligence.models.enums import ActorRole, ActorType, ExtractionMethod, ScriptType

if TYPE_CHECKING:
    from intelligence.extraction.places import PlaceMention

PROVIDER = "intelligence.extraction.actors"
ID_PREFIX = "ACT"
METHOD = ExtractionMethod.DICTIONARY

#: A name may sit behind an abbreviation, so the period between initials is part of the phrase.
NAME_GAP = re.compile(r"[ .]*")

#: Tamil personal names carry this marker; verbs and common nouns in front of a title do not.
NAME_TERMINATION = "ன்"

CONFIDENCE_TITLE = 0.85
CONFIDENCE_BODY = 0.8
CONFIDENCE_COLLECTIVE = 0.75
CONFIDENCE_NAMED = 0.05
CONFIDENCE_AFFILIATED = 0.05
MAX_CONFIDENCE = 0.95

UNCUED = "uncued"

_TITLE_KINDS = frozenset({"office-title"})
_BASE_CONFIDENCE = {
    "office-title": CONFIDENCE_TITLE,
    "body-head": CONFIDENCE_BODY,
    "collective-head": CONFIDENCE_COLLECTIVE,
}

_CUE_ORDER = {role: index for index, role in enumerate(vocabulary.ROLE_PRECEDENCE)}

# Latin cues match as whole words; Tamil cues are stems that take a case or tense ending.
_CUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        kind,
        re.compile(rf"\b{re.escape(cue)}\b", re.IGNORECASE)
        if cue.isascii()
        else re.compile(re.escape(cue)),
    )
    for kind, cues in vocabulary.EVENT_CUES.items()
    for cue in cues
)


class ActorError(ValueError):
    """An entity could not be read against the field it came from."""


@dataclass(frozen=True)
class Entity:
    """One entity phrase exactly as the field printed it, with the head that licensed it."""

    kind: str
    field: str
    surface: str
    span: Span
    entry: str
    actor_type: ActorType
    head: str
    label: str
    rule: str
    modifiers: tuple[str, ...] = ()
    modifier_folds: tuple[str, ...] = ()
    title: Optional[str] = None
    name: Optional[str] = None
    sentence: int = 0
    context: str = ""

    @property
    def char_start(self) -> int:
        return self.span.char_start

    @property
    def char_end(self) -> int:
        return self.span.char_end

    @property
    def coalesce_key(self) -> tuple:
        return (self.entry, self.actor_type, mention_text.fold(self.name or ""))

    def describe(self) -> str:
        return f"{self.kind} {self.surface!r} as {self.entry} ({self.actor_type.value})"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "field": self.field,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "entry": self.entry,
            "actor_type": self.actor_type.value,
            "head": self.head,
            "modifiers": list(self.modifiers),
            "title": self.title,
            "name": self.name,
            "label": self.label,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class Cue:
    """The words in the sentence that tie this entity to the event."""

    kind: str
    role: ActorRole
    text: str
    span: Span
    credited: bool
    evidence: Evidence

    def describe(self) -> str:
        state = self.role.value if self.credited else "uncertain"
        return (
            f"{self.kind} cue {self.text!r} "
            f"[{self.span.char_start}:{self.span.char_end}] -> {state}"
        )

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "role": self.role.value,
            "text": self.text,
            "char_start": self.span.char_start,
            "char_end": self.span.char_end,
            "credited": self.credited,
            "evidence_id": self.evidence.evidence_id,
        }


@dataclass(frozen=True)
class ActorOccurrence:
    """One printing of one entity, with the cue its own sentence supplied."""

    entity: Entity
    phrase_evidence: Evidence
    cue: Optional[Cue] = None

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        if self.cue is None:
            return (self.phrase_evidence,)
        return (self.phrase_evidence, self.cue.evidence)

    def describe(self) -> str:
        return f"{self.entity.field} [{self.entity.char_start}:{self.entity.char_end}]"


@dataclass(frozen=True)
class ActorRefusal:
    """An entity the text never tied to anything, kept so the drop is visible."""

    field: str
    surface: str
    char_start: int
    char_end: int
    entry: str
    reason: str
    note: str

    def describe(self) -> str:
        return f"{self.surface!r} ({self.entry}) in {self.field} refused: {self.reason}"

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "surface": self.surface,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "entry": self.entry,
            "reason": self.reason,
            "note": self.note,
        }


def _refused_as_entity(word: Word) -> bool:
    return any(mention_text.not_an_entity(candidate.form) for candidate in word.candidates)


def _is_name_token(word: Word) -> bool:
    """A token the text is using as somebody's name, not one it is using as a word."""
    if word.is_number or _refused_as_entity(word):
        return False
    if word.entry(ACTOR_LEXICON) is not None or word.entry(PLACE_LEXICON) is not None:
        return False
    if word.is_latin:
        return word.capitalised
    return word.text.endswith(NAME_TERMINATION)


def _accepts_modifier(word: Word, *, title_head: bool) -> bool:
    """An actor head only modifies an office title, so "மாநகராட்சி பக்தர்கள்" stays two parties."""
    if _refused_as_entity(word):
        return False
    if word.entry(ACTOR_LEXICON) is not None:
        return title_head
    if word.direction or mention_text.fold(word.text) in _QUALIFIER_FOLDED:
        return True
    if word.entry(PLACE_LEXICON) is not None:
        return True
    return word.is_latin and word.capitalised


_QUALIFIER_FOLDED = frozenset(mention_text.fold(word) for word in vocabulary.PLACE_QUALIFIERS)


def _name_run(prose: Prose, head: int) -> tuple[int, ...]:
    """The tokens after a title that the text is using as that person's name."""
    words = prose.words
    taken: list[int] = []
    index = head + 1
    while index < len(words) and len(taken) < vocabulary.MAX_PERSON_NAME_TOKENS:
        candidate = words[index]
        previous = words[taken[-1]] if taken else words[head]
        if candidate.sentence != previous.sentence:
            break
        if not NAME_GAP.fullmatch(prose.text[previous.char_end : candidate.char_start]):
            break
        if not _is_name_token(candidate):
            break
        taken.append(index)
        index += 1
    return tuple(taken)


def find_entities(prose: Prose) -> tuple[Entity, ...]:
    if prose.is_empty:
        return ()

    words = prose.words
    found: list[Entity] = []
    for index, word in enumerate(words):
        match = word.entry(ACTOR_LEXICON)
        if match is None:
            continue
        title = match.entry.kind in _TITLE_KINDS
        head_run = mention_text.phrase(
            prose, index, accept=partial(_accepts_modifier, title_head=title)
        )
        name_run = _name_run(prose, index) if title else ()
        run = (*head_run, *name_run)
        span = mention_text.quote_of(words, run[0], run[-1] + 1, prose.text)
        modifiers = tuple(i for i in head_run if i < index)
        found.append(
            Entity(
                kind=match.entry.kind,
                field=prose.field,
                surface=span.quote,
                span=span,
                entry=match.entry.surface,
                actor_type=match.entry.row.actor_type,
                head=word.text,
                label=match.label,
                rule=match.rule,
                modifiers=tuple(words[i].text for i in modifiers),
                modifier_folds=tuple(mention_text.fold(words[i].text) for i in modifiers),
                title=(
                    mention_text.quote_of(
                        words, head_run[0], head_run[-1] + 1, prose.text
                    ).quote
                    if title
                    else None
                ),
                name=(
                    mention_text.quote_of(words, name_run[0], name_run[-1] + 1, prose.text).quote
                    if name_run
                    else None
                ),
                sentence=word.sentence,
                context=prose.context_window(index),
            )
        )
    return _longest_wins(found)


def _longest_wins(entities: Sequence[Entity]) -> tuple[Entity, ...]:
    """The longest reading of a run of words, so a title never loses its body to a bare head."""
    kept: list[Entity] = []
    for entity in sorted(
        entities, key=lambda item: (-(item.char_end - item.char_start), item.char_start)
    ):
        if any(_overlaps(entity.span, other.span) for other in kept):
            continue
        kept.append(entity)
    kept.sort(key=lambda item: item.char_start)
    return tuple(kept)


def _overlaps(one: Span, other: Span) -> bool:
    return one.char_start < other.char_end and other.char_start < one.char_end


def _cue_for(prose: Prose, entity: Entity) -> Optional[tuple[str, int, int]]:
    """The cue nearest this entity inside the sentence the entity itself stands in."""
    start, end = prose.sentence_bounds(entity.sentence)
    sentence = prose.text[start:end]
    best: Optional[tuple[int, int, str, int]] = None
    for kind, pattern in _CUE_PATTERNS:
        for hit in pattern.finditer(sentence):
            from_, until = start + hit.start(), start + hit.end()
            if from_ < entity.char_end and entity.char_start < until:
                continue
            distance = min(abs(from_ - entity.char_start), abs(until - entity.char_end))
            if best is None or (distance, hit.start()) < (best[0], best[1]):
                best = (distance, hit.start(), kind, hit.end() - hit.start())
    if best is None:
        return None
    return best[2], start + best[1], start + best[1] + best[3]


def _credits(kind: str, actor_type: ActorType) -> bool:
    if kind == vocabulary.CUE_RESPONSE:
        return actor_type in vocabulary.AUTHORITY_ACTOR_TYPES
    if kind == vocabulary.CUE_DEMAND:
        return actor_type in vocabulary.DEMAND_ACTOR_TYPES
    return True


def _phrase_evidence(source: SourceField, entity: Entity) -> Evidence:
    return source.evidence_at(
        entity.span,
        method=METHOD,
        confidence=_BASE_CONFIDENCE[entity.kind],
        notes=(
            f"{entity.kind} surface read by the {entity.rule} rule against the entry "
            f"{entity.entry!r}"
        ),
    )


def _cue(source: SourceField, prose: Prose, entity: Entity) -> Optional[Cue]:
    found = _cue_for(prose, entity)
    if found is None:
        return None
    kind, char_start, char_end = found
    span = Span(quote=source.text[char_start:char_end], char_start=char_start, char_end=char_end)
    credited = _credits(kind, entity.actor_type)
    return Cue(
        kind=kind,
        role=vocabulary.CUE_ROLES[kind] if credited else ActorRole.UNKNOWN,
        text=span.quote,
        span=span,
        credited=credited,
        evidence=source.evidence_at(
            span,
            method=METHOD,
            notes=(
                f"{kind} cue: the words in this sentence that tie {entity.surface!r} to "
                "what happened"
            ),
        ),
    )


@dataclass(frozen=True)
class ActorMention:
    """One actor this record states, every span that states it, and the role the text gave it."""

    actor_id: str
    entity: Entity
    occurrences: tuple[ActorOccurrence, ...]
    role: ActorRole = ActorRole.UNKNOWN
    cue: Optional[Cue] = None
    affiliation_mention_id: Optional[str] = None
    affiliation_surface: Optional[str] = None

    @property
    def surface(self) -> str:
        return self.entity.surface

    @property
    def actor_type(self) -> ActorType:
        return self.entity.actor_type

    @property
    def field(self) -> str:
        return self.entity.field

    @property
    def char_start(self) -> int:
        return self.entity.char_start

    @property
    def char_end(self) -> int:
        return self.entity.char_end

    @property
    def official_title(self) -> Optional[str]:
        return self.entity.title

    @property
    def is_named(self) -> bool:
        return self.entity.name is not None or self.affiliation_mention_id is not None

    @property
    def confidence(self) -> Optional[float]:
        if self.role is ActorRole.UNKNOWN:
            return None
        base = _BASE_CONFIDENCE[self.entity.kind]
        if self.entity.name:
            base += CONFIDENCE_NAMED
        if self.affiliation_mention_id is not None:
            base += CONFIDENCE_AFFILIATED
        return round(min(MAX_CONFIDENCE, base), 4)

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        unique: dict[str, Evidence] = {}
        for occurrence in self.occurrences:
            for evidence in occurrence.evidence:
                unique.setdefault(evidence.evidence_id, evidence)
        return tuple(unique.values())

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(evidence.evidence_id for evidence in self.evidence)

    @property
    def name_text(self) -> str:
        return self.entity.surface

    @property
    def name_normalized(self) -> str:
        return normalize_text(self.entity.surface).text

    @property
    def script(self) -> ScriptType:
        return language.assess(self.entity.surface).profile.script_type

    @property
    def language_code(self) -> str:
        return language.assess(self.entity.surface).primary_language

    @property
    def transliterated_latin(self) -> Optional[str]:
        return _transliterate(self.entity.surface)

    @property
    def title_transliterated(self) -> Optional[str]:
        title = self.official_title
        if title is None:
            return None
        for token in morphology.tokenize(title):
            stated = _stated_latin(token.quote)
            if stated:
                return stated[0]
        return _transliterate(title)

    @property
    def notes(self) -> str:
        parts = [f"{self.entity.kind} head {self.entity.entry!r} read by the {self.entity.rule} rule"]
        if self.entity.modifiers:
            parts.append("the text qualifies it with " + " ".join(self.entity.modifiers))
        if self.entity.name:
            parts.append(f"named {self.entity.name!r} in the same phrase")
        if self.affiliation_surface is not None:
            parts.append(
                f"the text stands it at the place it also mentions as {self.affiliation_surface!r}"
            )
        if self.cue is not None:
            parts.append(
                self.cue.describe()
                if self.cue.credited
                else f"{self.cue.describe()}: {self.actor_type.value} cannot play that role here"
            )
        if len(self.occurrences) > 1:
            parts.append("printed at " + ", ".join(o.describe() for o in self.occurrences))
        return "; ".join(parts)

    def to_model(self) -> Actor:
        return Actor(
            actor_id=self.actor_id,
            name_text=self.name_text,
            name_normalized=self.name_normalized or None,
            transliterated_latin=self.transliterated_latin,
            official_title=self.official_title,
            official_title_transliterated=self.title_transliterated,
            actor_type=self.actor_type,
            role=self.role,
            script=self.script,
            language=self.language_code,
            affiliation_mention_id=self.affiliation_mention_id,
            is_named=self.is_named,
            method=METHOD,
            confidence=self.confidence,
            evidence_ids=list(self.evidence_ids),
            notes=self.notes,
        )

    def describe(self) -> str:
        return (
            f"{self.actor_id} {self.role.value} {self.surface!r} "
            f"({self.actor_type.value}) from {len(self.occurrences)} span(s), "
            f"first at {self.field} [{self.char_start}:{self.char_end}]"
        )

    def as_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "role": self.role.value,
            "kind": self.entity.kind,
            "rule": self.entity.rule,
            "entry": self.entity.entry,
            "name_text": self.name_text,
            "name_normalized": self.name_normalized,
            "transliterated_latin": self.transliterated_latin,
            "official_title": self.official_title,
            "official_title_transliterated": self.title_transliterated,
            "actor_type": self.actor_type.value,
            "is_named": self.is_named,
            "modifiers": list(self.entity.modifiers),
            "person_name": self.entity.name,
            "field": self.field,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "confidence": self.confidence,
            "occurrences": [o.describe() for o in self.occurrences],
            "evidence_ids": list(self.evidence_ids),
            "cue": None if self.cue is None else self.cue.as_dict(),
            "affiliation_mention_id": self.affiliation_mention_id,
        }


def _stated_latin(surface: str) -> tuple[str, ...]:
    for lexicon in (ACTOR_LEXICON, PLACE_LEXICON):
        match = lexicon.match(surface)
        if match is not None and match.entry.latin:
            return match.entry.latin
    return ()


def _transliterate(surface: str) -> Optional[str]:
    forms: list[str] = []
    for token in morphology.tokenize(surface):
        if token.quote.isascii():
            forms.append(token.quote)
            continue
        stated = _stated_latin(token.quote)
        if stated:
            forms.append(stated[0])
            continue
        found = transliteration.candidates(token.quote)
        if not found:
            return None
        forms.append(found[0].latin)
    return " ".join(forms) or None


def _link(
    entity: Entity, mentions: Sequence[PlaceMention]
) -> tuple[Optional[str], Optional[str]]:
    """The place mention whose printed words are this entity's own qualifier, when only one says so."""
    for index, wanted in enumerate(entity.modifier_folds):
        matches = [mention for mention in mentions if mention_text.fold(mention.surface) == wanted]
        if not matches:
            continue
        if len(matches) == 1 or len({mention.expression.entry for mention in matches}) == 1:
            return matches[0].mention_id, entity.modifiers[index]
    return None, None


@dataclass(frozen=True)
class ActorExtraction:
    """Everything one record's text states about who was involved."""

    record_id: str
    actors: tuple[ActorMention, ...] = ()
    refusals: tuple[ActorRefusal, ...] = ()

    def model_actors(self) -> list[Actor]:
        return [actor.to_model() for actor in self.actors]

    def affiliation_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                actor.affiliation_mention_id
                for actor in self.actors
                if actor.affiliation_mention_id is not None
            )
        )

    def evidence(self) -> tuple[Evidence, ...]:
        unique: dict[str, Evidence] = {}
        for actor in self.actors:
            for evidence in actor.evidence:
                unique.setdefault(evidence.evidence_id, evidence)
        return tuple(unique.values())

    def warnings(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                f"actor candidate {refusal.surface!r} in {refusal.field} "
                f"[{refusal.char_start}:{refusal.char_end}] refused: {refusal.reason}"
                for refusal in self.refusals
            )
        )

    def describe(self) -> str:
        return f"{len(self.actors)} actor(s), {len(self.refusals)} refused"

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "actors": [actor.as_dict() for actor in self.actors],
            "refusals": [refusal.as_dict() for refusal in self.refusals],
            "affiliation_ids": list(self.affiliation_ids()),
        }


def _best_cue(occurrences: Sequence[ActorOccurrence]) -> tuple[ActorRole, Optional[Cue]]:
    """The role the strongest cue in any of these sentences gives, and the cue that gave it."""
    credited = [
        (occurrence.cue, occurrence.cue.role)
        for occurrence in occurrences
        if occurrence.cue is not None and occurrence.cue.credited
    ]
    if credited:
        cue, role = min(credited, key=lambda pair: (_CUE_ORDER[pair[1]], pair[0].span.char_start))
        return role, cue
    uncertain = next((occurrence.cue for occurrence in occurrences if occurrence.cue is not None), None)
    return ActorRole.UNKNOWN, uncertain


def _grouped(
    readings: Sequence[tuple[SourceField, Prose]],
) -> list[tuple[Entity, tuple[ActorOccurrence, ...]]]:
    grouped: dict[tuple, list[ActorOccurrence]] = {}
    heads: dict[tuple, Entity] = {}
    order: list[tuple] = []
    for source, prose in readings:
        for entity in find_entities(prose):
            occurrence = ActorOccurrence(
                entity=entity,
                phrase_evidence=_phrase_evidence(source, entity),
                cue=_cue(source, prose, entity),
            )
            key = entity.coalesce_key
            if key not in grouped:
                grouped[key] = []
                heads[key] = entity
                order.append(key)
            grouped[key].append(occurrence)
    return [(heads[key], tuple(grouped[key])) for key in order]


def _actor_id(record_id: str, position: int) -> str:
    return f"{ID_PREFIX}-{record_id}-{position}"


def extract(
    readings: Sequence[tuple[SourceField, Prose]],
    *,
    place_mentions: Sequence[PlaceMention] = (),
) -> ActorExtraction:
    if not readings:
        raise ActorError("no field was read, so no actor can be named")

    record_id = readings[0][0].record_id
    grouped = _grouped(readings)

    tied: list[tuple[Entity, tuple[ActorOccurrence, ...], ActorRole, Cue]] = []
    refusals: list[ActorRefusal] = []
    for entity, occurrences in grouped:
        role, cue = _best_cue(occurrences)
        if cue is None:
            refusals.append(
                ActorRefusal(
                    field=entity.field,
                    surface=entity.surface,
                    char_start=entity.char_start,
                    char_end=entity.char_end,
                    entry=entity.entry,
                    reason=UNCUED,
                    note=(
                        f"{entity.surface!r} names an entity, but nothing in the sentence it "
                        "stands in says anything happening"
                    ),
                )
            )
            continue
        tied.append((entity, occurrences, role, cue))

    actors: list[ActorMention] = []
    for position, (entity, occurrences, role, cue) in enumerate(tied, start=1):
        affiliation, affiliation_surface = _link(entity, place_mentions)
        actors.append(
            ActorMention(
                actor_id=_actor_id(record_id, position),
                entity=entity,
                occurrences=occurrences,
                role=role,
                cue=cue,
                affiliation_mention_id=affiliation,
                affiliation_surface=affiliation_surface,
            )
        )
    return ActorExtraction(
        record_id=record_id,
        actors=tuple(actors),
        refusals=tuple(refusals),
    )


__all__ = [
    "ActorError",
    "ActorExtraction",
    "ActorMention",
    "ActorOccurrence",
    "ActorRefusal",
    "Cue",
    "Entity",
    "ID_PREFIX",
    "METHOD",
    "PROVIDER",
    "UNCUED",
    "extract",
    "find_entities",
]
