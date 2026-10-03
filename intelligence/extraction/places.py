"""Place mentions: the role each place the text named plays, cited where it was printed and resolved nowhere."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence

from intelligence.config import mention_words as vocabulary
from intelligence.extraction import language, mention_text, morphology, transliteration
from intelligence.extraction import place_expressions as surfaces
from intelligence.extraction.normalize import normalize as normalize_text
from intelligence.extraction.place_expressions import PlaceExpression, PlaceFinding, Rejection
from intelligence.extraction.spans import SourceField, Span
from intelligence.models.enums import (
    DistrictHintAuthority,
    ExtractionMethod,
    GranularityLevel,
    MentionRole,
    MentionType,
)
from intelligence.models.evidence import Evidence
from intelligence.models.spatial import LocationMention, SpatialHint

PROVIDER = "intelligence.extraction.places"
ID_PREFIX = "LOC"
METHOD = ExtractionMethod.DICTIONARY

#: A place the lede puts the event at has to be in a case that holds it there.
EVENT_LOCATION_KINDS = frozenset(
    {surfaces.CASE_MARKED, surfaces.TYPE_HEAD, surfaces.KNOWN_NAME, surfaces.LATIN_NAME}
)
EVENT_CONTAINER_KINDS = frozenset(
    {surfaces.KNOWN_NAME, surfaces.TYPE_HEAD, surfaces.LATIN_NAME}
)
_INSTITUTION_TYPES = frozenset({MentionType.INSTITUTION, MentionType.GOVERNMENT_BODY})
_EVENT_ROLES = frozenset({MentionRole.EVENT_LOCATION, MentionRole.EVENT_CONTAINER})


class PlaceError(ValueError):
    """A place finding could not be read against the field it came from."""


@dataclass(frozen=True)
class Occurrence:

    expression: PlaceExpression
    evidence: Evidence

    @property
    def span(self) -> Span:
        return self.expression.span

    def describe(self) -> str:
        return f"{self.expression.field} [{self.span.char_start}:{self.span.char_end}]"


@dataclass(frozen=True)
class PlaceMention:
    """One place this record states, every span that states it, and the role it plays."""

    mention_id: str
    expression: PlaceExpression
    occurrences: tuple[Occurrence, ...]
    role: MentionRole = MentionRole.MENTIONED_ONLY

    @property
    def kind(self) -> str:
        return self.expression.kind

    @property
    def field(self) -> str:
        return self.expression.field

    @property
    def surface(self) -> str:
        return self.expression.surface

    @property
    def raw_text(self) -> str:
        return self.surface

    @property
    def span(self) -> Span:
        return self.expression.span

    @property
    def char_start(self) -> int:
        return self.expression.char_start

    @property
    def char_end(self) -> int:
        return self.expression.char_end

    @property
    def mention_type(self) -> MentionType:
        return self.expression.mention_type

    @property
    def granularity(self) -> GranularityLevel:
        return self.expression.granularity

    @property
    def names(self) -> tuple[str, ...]:
        return self.expression.names

    @property
    def confidence(self) -> float:
        return max(occurrence.expression.confidence for occurrence in self.occurrences)

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(occurrence.evidence for occurrence in self.occurrences)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(evidence.evidence_id for evidence in self.evidence))

    @property
    def resolved(self) -> bool:
        return bool(self.evidence_ids)

    @property
    def is_event_role(self) -> bool:
        return self.role in _EVENT_ROLES

    @property
    def text_normalized(self) -> str:
        return normalize_text(self.surface).text

    @property
    def transliterated_latin(self) -> Optional[str]:
        forms: list[str] = []
        for token in morphology.tokenize(self.surface):
            if token.quote.isascii():
                forms.append(token.quote)
                continue
            found = transliteration.candidates(token.quote)
            if not found:
                return None
            forms.append(found[0].latin)
        return " ".join(forms) or None

    @property
    def notes(self) -> str:
        parts = [
            f"{self.kind} surface read by the {self.expression.rule} rule: {self.expression.note}"
        ]
        if len(self.occurrences) > 1:
            parts.append("printed at " + ", ".join(o.describe() for o in self.occurrences))
        if self.role is MentionRole.EVENT_LOCATION:
            parts.append(
                "the lede puts the event at this place"
                if _is_locative(self.expression)
                else "the lede names this place and Latin script prints no case ending, so the "
                "place the lede names is the place it reports"
            )
        if self.role is MentionRole.EVENT_CONTAINER:
            parts.append("the headline names no event place, so the broadest place it states holds it")
        if self.role is MentionRole.REPORTING_ORIGIN:
            parts.append("a dateline: where the report was written, not where anything happened")
        return "; ".join(parts)

    def to_model(self) -> LocationMention:
        assessment = language.assess(self.surface)
        return LocationMention(
            mention_id=self.mention_id,
            text=self.surface,
            text_normalized=self.text_normalized,
            transliterated_latin=self.transliterated_latin,
            script=assessment.profile.script_type,
            language=assessment.primary_language,
            mention_type=self.mention_type,
            granularity=self.granularity,
            role=self.role,
            context_window=self.expression.context,
            is_event_location_candidate=self.is_event_role,
            gazetteer_matched=False,
            method=METHOD,
            confidence=self.confidence,
            evidence_ids=list(self.evidence_ids),
            notes=self.notes,
        )

    def describe(self) -> str:
        return (
            f"{self.mention_id} {self.role.value} {self.surface!r} "
            f"({self.mention_type.value}/{self.granularity.value}) from "
            f"{len(self.occurrences)} span(s), first at {self.field} "
            f"[{self.char_start}:{self.char_end}]"
        )

    def as_dict(self) -> dict:
        return {
            "mention_id": self.mention_id,
            "role": self.role.value,
            "kind": self.kind,
            "rule": self.expression.rule,
            "via": self.expression.via,
            "entry": self.expression.entry,
            "text": self.surface,
            "text_normalized": self.text_normalized,
            "transliterated_latin": self.transliterated_latin,
            "mention_type": self.mention_type.value,
            "granularity": self.granularity.value,
            "names": list(self.names),
            "field": self.field,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "confidence": self.confidence,
            "occurrences": [o.describe() for o in self.occurrences],
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class DistrictText:
    """The place the article itself says is the district, when it says so."""

    mention_id: str
    surface: str
    field: str
    char_start: int
    char_end: int
    confidence: float
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "mention_id": self.mention_id,
            "surface": self.surface,
            "field": self.field,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class PlaceExtraction:

    record_id: str
    mentions: tuple[PlaceMention, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    best_event_location_mention_id: Optional[str] = None
    district_text: Optional[DistrictText] = None
    competing_districts: tuple[str, ...] = ()

    def mention_by_id(self, mention_id: str) -> Optional[PlaceMention]:
        return next((m for m in self.mentions if m.mention_id == mention_id), None)

    def evidence(self) -> tuple[Evidence, ...]:
        collected: dict[str, Evidence] = {}
        for mention in self.mentions:
            for evidence in mention.evidence:
                collected.setdefault(evidence.evidence_id, evidence)
        return tuple(collected.values())

    def with_roles(self, affiliations: Iterable[str]) -> "PlaceExtraction":
        """Hand the places an actor is attached to their own role, before models are built."""
        wanted = set(affiliations)
        mentions = tuple(
            replace(
                mention,
                role=MentionRole.ACTOR_AFFILIATION,
            )
            if mention.mention_id in wanted and mention.role is MentionRole.MENTIONED_ONLY
            else mention
            for mention in self.mentions
        )
        return replace(self, mentions=mentions)

    def location_mentions(self) -> list[LocationMention]:
        return [mention.to_model() for mention in self.mentions]

    def warnings(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                f"place candidate {rejection.surface!r} in {rejection.field} "
                f"[{rejection.char_start}:{rejection.char_end}] refused: {rejection.reason}"
                for rejection in self.rejections
            )
        )

    def describe(self) -> str:
        return (
            f"{len(self.mentions)} place mention(s), "
            f"{len(self.rejections)} refused, event place "
            f"{self.best_event_location_mention_id or 'none'}"
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "mentions": [mention.as_dict() for mention in self.mentions],
            "rejections": [rejection.as_dict() for rejection in self.rejections],
            "best_event_location_mention_id": self.best_event_location_mention_id,
            "district_text": (
                self.district_text.as_dict() if self.district_text else None
            ),
            "competing_districts": list(self.competing_districts),
        }


def _evidence(source: SourceField, expression: PlaceExpression) -> Evidence:
    return source.evidence_at(
        expression.span,
        method=METHOD,
        confidence=expression.confidence,
        notes=(
            f"{expression.kind} place surface, read by the {expression.rule} rule against "
            f"the lexicon entry {expression.entry!r}"
        ),
    )


def _is_locative(expression: PlaceExpression) -> bool:
    return expression.label in vocabulary.LOCATIVE_LABELS


def _locates(expression: PlaceExpression) -> bool:
    """Whether the text puts the event here; Latin prints no case, so a named lede phrase counts."""
    if _is_locative(expression):
        return True
    if not expression.names or not expression.surface.isascii():
        return False
    return expression.kind in (surfaces.KNOWN_NAME, surfaces.TYPE_HEAD, surfaces.LATIN_NAME)


def _specificity(expression: PlaceExpression) -> int:
    return vocabulary.GRANULARITY_SPECIFICITY[expression.granularity]


def _lede_location(
    ordered: Sequence[PlaceExpression], body_field: Optional[str]
) -> Optional[PlaceExpression]:
    """The place the first sentence of the body puts the event at, if it puts it anywhere."""
    candidates = [
        expression
        for expression in ordered
        if expression.field == body_field
        and expression.sentence == 0
        and expression.kind in EVENT_LOCATION_KINDS
        and _locates(expression)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda expression: (
            0 if expression.names else 1,
            _specificity(expression),
            expression.char_start,
        ),
    )


def _headline_container(
    ordered: Sequence[PlaceExpression], title_field: Optional[str]
) -> Optional[PlaceExpression]:
    """The broadest place the headline holds the event inside, when the lede names no place."""
    candidates = [
        expression
        for expression in ordered
        if expression.field == title_field
        and expression.kind in EVENT_CONTAINER_KINDS
        and expression.granularity in surfaces.CONTAINER_LEVELS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda expression: (_specificity(expression), expression.char_start))


def _latin_forms(surface: str) -> tuple[str, ...]:
    match = mention_text.PLACE_LEXICON.match(surface)
    return () if match is None else match.entry.latin


def _agrees(surface: str, hint: str) -> bool:
    wanted = hint.strip().casefold()
    if mention_text.fold(surface) == mention_text.fold(hint):
        return True
    if surface.strip().casefold() == wanted:
        return True
    return any(form.strip().casefold() == wanted for form in _latin_forms(surface))


def _district_reading(
    mentions: Sequence[PlaceMention], hint: Optional[str]
) -> tuple[Optional[DistrictText], tuple[str, ...]]:
    """Which district the article says this is, in the article's own words, if it says one."""
    if hint is None:
        return None, ()
    agreed: Optional[DistrictText] = None
    competing: list[str] = []
    for mention in mentions:
        expression = mention.expression
        if expression.kind != surfaces.TYPE_HEAD:
            continue
        if expression.granularity is not GranularityLevel.DISTRICT:
            continue
        if not expression.names:
            continue
        if any(_agrees(name, hint) for name in expression.names):
            if agreed is None:
                agreed = DistrictText(
                    mention_id=mention.mention_id,
                    surface=expression.surface,
                    field=expression.field,
                    char_start=expression.char_start,
                    char_end=expression.char_end,
                    confidence=mention.confidence,
                    evidence_ids=mention.evidence_ids,
                )
            continue
        label = next(
            (form for name in expression.names for form in _latin_forms(name)),
            None,
        ) or " ".join(expression.names)
        if label not in competing:
            competing.append(label)
    return agreed, tuple(competing)


def _role_for(
    expression: PlaceExpression, event_key: Optional[tuple], location_found: bool
) -> MentionRole:
    key = expression.coalesce_key
    if expression.kind == surfaces.DATELINE:
        return MentionRole.REPORTING_ORIGIN
    if key == event_key:
        return MentionRole.EVENT_LOCATION if location_found else MentionRole.EVENT_CONTAINER
    if (
        expression.names
        and (
            expression.mention_type in _INSTITUTION_TYPES
            or expression.granularity is GranularityLevel.LANDMARK
        )
    ):
        return MentionRole.INSTITUTION_NAME
    return MentionRole.MENTIONED_ONLY


def _mention_id(record_id: str, position: int) -> str:
    return f"{ID_PREFIX}-{record_id}-{position}"


def extract(
    readings: Sequence[tuple[SourceField, PlaceFinding]],
    *,
    title_field: Optional[str] = None,
    body_field: Optional[str] = None,
    district_hint: Optional[str] = None,
) -> PlaceExtraction:
    if not readings:
        raise PlaceError("no field was read, so no place can be mentioned")

    record_id = readings[0][0].record_id
    sources: dict[str, SourceField] = {}
    ordered: list[PlaceExpression] = []
    rejections: list[Rejection] = []
    for source, finding in readings:
        if finding.field != source.field:
            raise PlaceError(
                f"the finding for {finding.field!r} is not the field {source.describe()}"
            )
        if sources.setdefault(source.field, source) is not source:
            raise PlaceError(f"{source.field!r} was read twice")
        ordered.extend(sorted(finding.expressions, key=lambda item: item.char_start))
        rejections.extend(finding.rejections)

    grouped: dict[tuple, list[Occurrence]] = {}
    for expression in ordered:
        occurrence = Occurrence(expression, _evidence(sources[expression.field], expression))
        grouped.setdefault(expression.coalesce_key, []).append(occurrence)

    location = _lede_location(ordered, body_field)
    container = None if location is not None else _headline_container(ordered, title_field)
    chosen = location if location is not None else container
    event_key = None if chosen is None else chosen.coalesce_key

    order = list(grouped)
    ids = iter(_mention_id(record_id, index) for index in range(1, len(order) + 1))
    event_id = None if event_key is None else _mention_id(record_id, order.index(event_key) + 1)

    mentions: list[PlaceMention] = []
    for key, group in grouped.items():
        head = group[0].expression
        mentions.append(
            PlaceMention(
                mention_id=next(ids),
                expression=head,
                occurrences=tuple(group),
                role=_role_for(head, event_key, location is not None),
            )
        )

    tuple_mentions = tuple(mentions)
    district, competing = _district_reading(tuple_mentions, district_hint)
    return PlaceExtraction(
        record_id=record_id,
        mentions=tuple_mentions,
        rejections=tuple(rejections),
        best_event_location_mention_id=(
            None if event_key is None else event_id
        ),
        district_text=district,
        competing_districts=competing,
    )


def apply(
    extraction: PlaceExtraction, hint: SpatialHint, *, notes: Optional[str] = None
) -> SpatialHint:
    """Write the extracted mentions into the hint Stage 5 left, leaving its resolution alone."""
    district = hint.district_hint
    authority = hint.district_hint_authority
    from_text = hint.district_hint_from_text
    confidence = hint.district_hint_confidence
    if extraction.district_text is not None and district is not None:
        authority = DistrictHintAuthority.TEXT_EVIDENCE
        from_text = extraction.district_text.surface
        confidence = extraction.district_text.confidence

    recorded = notes or (
        f"Stage 6 read {len(extraction.mentions)} place mention(s) from the record's own text; "
        "no geography resolved, so resolution is still GIS's to do"
    )
    previous = hint.notes
    return SpatialHint(
        mentions=extraction.location_mentions(),
        best_event_location_mention_id=extraction.best_event_location_mention_id,
        district_hint=district,
        district_hint_authority=authority,
        district_hint_confidence=confidence,
        district_hint_from_text=from_text,
        competing_districts=list(
            dict.fromkeys((*hint.competing_districts, *extraction.competing_districts))
        ),
        sub_district_hint=hint.sub_district_hint,
        locality_hints=list(hint.locality_hints),
        resolution_state=hint.resolution_state,
        gis=hint.gis,
        notes="; ".join(part for part in (previous, recorded) if part),
    )


__all__ = [
    "EVENT_CONTAINER_KINDS",
    "EVENT_LOCATION_KINDS",
    "ID_PREFIX",
    "METHOD",
    "PROVIDER",
    "DistrictText",
    "Occurrence",
    "PlaceError",
    "PlaceExtraction",
    "PlaceMention",
    "apply",
    "extract",
]
