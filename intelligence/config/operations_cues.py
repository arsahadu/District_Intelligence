"""Stage 8b signal vocabulary: what a source printed about whether a situation is still running.

Surfaces are matched word by word against the field's own running text. A surface ending in
``*`` is a stem: it matches any printed word that begins with it, so one row covers
``தொடர்கிறான்`` through ``தொடர்கிறார்கள்``. No row here names an event type, and no row here
carries a severity verdict on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from intelligence.models.enums import (
    ObservationKind,
    OperationalState,
    SeverityCueCategory,
    SeverityLevel,
    SignalTier,
)

OPERATIONS_LEXICON_VERSION = "2026.10-stage8b"

#: Marks a surface as a stem rather than a whole printed word.
STEM_MARKER = "*"

ESCALATING = "escalating"
MITIGATING = "mitigating"
DIRECTIONS = (ESCALATING, MITIGATING)

#: A cue is only believed once its weight times the match quality clears this floor.
ACCEPTANCE_FLOOR = 0.30

#: How many words on either side of a cue are read for a "not" before the cue is believed.
NEGATION_WINDOW = 3

#: Fused Tamil negation tails; read as substrings because they bolt onto the verb.
NEGATION_SUBSTRINGS: tuple[str, ...] = ("இல்ல", "வில்லை", "அற்ற", "ஒல்ல")

#: English negation, matched as whole words so "notable" and "Noida" stay innocent.
NEGATION_WORDS: frozenset[str] = frozenset(
    {"not", "no", "never", "none", "neither", "without", "n't"}
)


@dataclass(frozen=True)
class StatusCue:
    """One printed wording that says the situation is still running, or has stopped."""

    surface: str
    state: OperationalState
    tier: SignalTier
    note: str
    latin: tuple[str, ...] = ()

    @property
    def claim(self) -> tuple[str, ...]:
        return (self.state.value, self.tier.value)


@dataclass(frozen=True)
class SeverityCue:
    """One printed wording that argues the event was at least this serious."""

    surface: str
    category: SeverityCueCategory
    band: SeverityLevel
    weight: float
    direction: str = ESCALATING
    note: str = ""
    count_kind: Optional[ObservationKind] = None
    latin: tuple[str, ...] = ()

    @property
    def claim(self) -> tuple[str, ...]:
        return (self.category.value, self.band.value, self.direction, str(self.weight))


@dataclass(frozen=True)
class CountHead:
    """The noun a stated number counts, so a quantity is typed rather than left as a bare digit."""

    surface: str
    kind: ObservationKind
    unit: Optional[str] = None
    note: str = ""

    @property
    def claim(self) -> tuple[str, ...]:
        return (self.kind.value,)


# fmt: off
STATUS_CUES: tuple[StatusCue, ...] = (
    # ongoing: the source says it in words
    StatusCue("தொடர்கிற*", OperationalState.ONGOING, SignalTier.STATED, "it continues"),
    StatusCue("தொடர்கின்ற*", OperationalState.ONGOING, SignalTier.STATED, "it continues"),
    StatusCue("தொடர்ந்தது", OperationalState.ONGOING, SignalTier.STATED, "it went on"),
    StatusCue("கண்காணித்து வரு*", OperationalState.ONGOING, SignalTier.STATED, "monitoring is going on"),
    StatusCue("அதிகரித்து வரு*", OperationalState.ONGOING, SignalTier.STATED, "it keeps increasing"),
    StatusCue("நடந்து வரு*", OperationalState.ONGOING, SignalTier.STATED, "it has been going on"),
    StatusCue("இயங்கிக் கொண்டிருக்கிற*", OperationalState.ONGOING, SignalTier.STATED, "it is still running"),
    StatusCue("தேங்கியுள்ள*", OperationalState.ONGOING, SignalTier.STATED, "water stands there now"),
    StatusCue("நீங்கவில்லை", OperationalState.ONGOING, SignalTier.STATED, "it has not gone down"),
    StatusCue("மாறவில்லை", OperationalState.ONGOING, SignalTier.STATED, "it has not changed"),
    StatusCue("தீரவில்லை", OperationalState.ONGOING, SignalTier.STATED, "it has not settled"),
    StatusCue("சரி செய்யப்படவில்லை", OperationalState.ONGOING, SignalTier.STATED, "it has not been fixed"),
    StatusCue("நீக்கப்படவில்லை", OperationalState.ONGOING, SignalTier.STATED, "it has not been removed"),
    StatusCue("மீட்கப்படவில்லை", OperationalState.ONGOING, SignalTier.STATED, "it has not been rescued"),
    StatusCue("கொண்டிருக்கிற*", OperationalState.ONGOING, SignalTier.STATED, "it is in the doing of it"),
    StatusCue("continues", OperationalState.ONGOING, SignalTier.STATED, "english: it continues"),
    StatusCue("continu*", OperationalState.ONGOING, SignalTier.STATED, "english: continuing, continued"),
    StatusCue("ongoing", OperationalState.ONGOING, SignalTier.STATED, "english: ongoing"),
    StatusCue("persist*", OperationalState.ONGOING, SignalTier.STATED, "english: it persists"),
    StatusCue("remains", OperationalState.ONGOING, SignalTier.STATED, "english: it remains"),
    StatusCue("unabated", OperationalState.ONGOING, SignalTier.STATED, "english: unabated"),
    StatusCue("not yet", OperationalState.ONGOING, SignalTier.STATED, "english: stated as not reached"),
    StatusCue("yet to be", OperationalState.ONGOING, SignalTier.STATED, "english: still awaiting"),
    StatusCue("has not been", OperationalState.ONGOING, SignalTier.STATED, "english: the doing is still owed"),
    StatusCue("being monitored", OperationalState.ONGOING, SignalTier.STATED, "english: under monitoring"),
    StatusCue("monitoring continues", OperationalState.ONGOING, SignalTier.STATED, "english: watch goes on"),
    StatusCue("operational status", OperationalState.ONGOING, SignalTier.STATED, "english: a live-status column"),
    # ongoing: the wording leans that way without saying it
    StatusCue("இன்னும்", OperationalState.ONGOING, SignalTier.IMPLIED, "still, yet"),
    StatusCue("நீங்காமல்", OperationalState.ONGOING, SignalTier.IMPLIED, "without letting up"),
    StatusCue("நடைபெறுகிற*", OperationalState.ONGOING, SignalTier.IMPLIED, "it is being held now"),
    StatusCue("பரவியுள்ள*", OperationalState.ONGOING, SignalTier.IMPLIED, "it has spread by now"),
    StatusCue("still", OperationalState.ONGOING, SignalTier.IMPLIED, "english: still"),
    StatusCue("under way", OperationalState.ONGOING, SignalTier.IMPLIED, "english: under way"),
    StatusCue("underway", OperationalState.ONGOING, SignalTier.IMPLIED, "english: underway"),
    StatusCue("in progress", OperationalState.ONGOING, SignalTier.IMPLIED, "english: in progress"),
    StatusCue("under monitoring", OperationalState.ONGOING, SignalTier.IMPLIED, "english: being watched"),
    # resolved: the stated situation has ended
    StatusCue("சரி செய்யப்பட்ட*", OperationalState.RESOLVED, SignalTier.STATED, "it was made right"),
    StatusCue("சீரமைக்கப்பட்ட*", OperationalState.RESOLVED, SignalTier.STATED, "it was rectified"),
    StatusCue("நீக்கப்பட்ட*", OperationalState.RESOLVED, SignalTier.STATED, "it was removed"),
    StatusCue("விலக்கப்பட்ட*", OperationalState.RESOLVED, SignalTier.STATED, "it was taken away"),
    StatusCue("திறக்கப்பட்ட*", OperationalState.RESOLVED, SignalTier.STATED, "it was opened again"),
    StatusCue("இயல்புநிலை", OperationalState.RESOLVED, SignalTier.STATED, "normal state returned to"),
    StatusCue("நிலைபெற்ற*", OperationalState.RESOLVED, SignalTier.STATED, "it settled"),
    StatusCue("தணிந்த*", OperationalState.RESOLVED, SignalTier.STATED, "it subsided"),
    StatusCue("வடிந்த*", OperationalState.RESOLVED, SignalTier.STATED, "it drained away"),
    StatusCue("குணமடை*", OperationalState.RESOLVED, SignalTier.STATED, "the patient improved"),
    StatusCue("கட்டுப்பாட்டுக்கு வந்த*", OperationalState.RESOLVED, SignalTier.STATED, "it came under control"),
    StatusCue("நிறைவேற*", OperationalState.RESOLVED, SignalTier.STATED, "the demand was met"),
    StatusCue("restored", OperationalState.RESOLVED, SignalTier.STATED, "english: restored"),
    StatusCue("resumed", OperationalState.RESOLVED, SignalTier.STATED, "english: service resumed"),
    StatusCue("reopened", OperationalState.RESOLVED, SignalTier.STATED, "english: reopened"),
    StatusCue("re-opened", OperationalState.RESOLVED, SignalTier.STATED, "english: re-opened"),
    StatusCue("cleared", OperationalState.RESOLVED, SignalTier.STATED, "english: cleared"),
    StatusCue("receded", OperationalState.RESOLVED, SignalTier.STATED, "english: water receded"),
    StatusCue("subsided", OperationalState.RESOLVED, SignalTier.STATED, "english: subsided"),
    StatusCue("normalised", OperationalState.RESOLVED, SignalTier.STATED, "english: back to normal"),
    StatusCue("normalized", OperationalState.RESOLVED, SignalTier.STATED, "english: back to normal"),
    StatusCue("back to normal", OperationalState.RESOLVED, SignalTier.STATED, "english: back to normal"),
    StatusCue("resolved", OperationalState.RESOLVED, SignalTier.STATED, "english: resolved"),
    StatusCue("lifted", OperationalState.RESOLVED, SignalTier.STATED, "english: the restriction lifted"),
    StatusCue("contained", OperationalState.RESOLVED, SignalTier.STATED, "english: the fire contained"),
    StatusCue("improved", OperationalState.RESOLVED, SignalTier.IMPLIED, "english: improved"),
    # closed: the file on it was shut
    StatusCue("முடிவுக்கு வந்த*", OperationalState.CLOSED, SignalTier.STATED, "it came to its end"),
    StatusCue("பணி நிறைவடை*", OperationalState.CLOSED, SignalTier.STATED, "the work was completed"),
    StatusCue("பணிகள் நிறைவடை*", OperationalState.CLOSED, SignalTier.STATED, "the works were completed"),
    StatusCue("வேலை நிறைவடை*", OperationalState.CLOSED, SignalTier.STATED, "the work was completed"),
    StatusCue("வேலைகள் நிறைவடை*", OperationalState.CLOSED, SignalTier.STATED, "the works were completed"),
    StatusCue("பணி முடிந்த*", OperationalState.CLOSED, SignalTier.STATED, "the work finished"),
    StatusCue("பணிகள் முடிந்த*", OperationalState.CLOSED, SignalTier.STATED, "the works finished"),
    StatusCue("புகார் மூடப்பட்ட*", OperationalState.CLOSED, SignalTier.STATED, "the complaint was closed"),
    StatusCue("வழக்கு முடிவுக்கு*", OperationalState.CLOSED, SignalTier.STATED, "the case came to its end"),
    StatusCue("case closed", OperationalState.CLOSED, SignalTier.STATED, "english: case closed"),
    StatusCue("complaint closed", OperationalState.CLOSED, SignalTier.STATED, "english: complaint closed"),
    StatusCue("ticket closed", OperationalState.CLOSED, SignalTier.STATED, "english: ticket closed"),
    StatusCue("incident closed", OperationalState.CLOSED, SignalTier.STATED, "english: incident closed"),
    StatusCue("officially closed", OperationalState.CLOSED, SignalTier.STATED, "english: officially closed"),
    StatusCue("work completed", OperationalState.CLOSED, SignalTier.STATED, "english: work completed"),
    StatusCue("works completed", OperationalState.CLOSED, SignalTier.STATED, "english: works completed"),
    StatusCue("task completed", OperationalState.CLOSED, SignalTier.STATED, "english: task completed"),
)

#: The record's own ``status`` field, which is a statement about the case, not about this pipeline.
DECLARED_STATUS_VALUES: dict[str, OperationalState] = {
    "ongoing": OperationalState.ONGOING,
    "open": OperationalState.ONGOING,
    "active": OperationalState.ONGOING,
    "in_progress": OperationalState.ONGOING,
    "pending": OperationalState.ONGOING,
    "unresolved": OperationalState.ONGOING,
    "unattended": OperationalState.ONGOING,
    "resolved": OperationalState.RESOLVED,
    "fixed": OperationalState.RESOLVED,
    "solved": OperationalState.RESOLVED,
    "rectified": OperationalState.RESOLVED,
    "closed": OperationalState.CLOSED,
    "completed": OperationalState.CLOSED,
    "done": OperationalState.CLOSED,
    "cancelled": OperationalState.CLOSED,
    "canceled": OperationalState.CLOSED,
    "rejected": OperationalState.CLOSED,
    "dismissed": OperationalState.CLOSED,
}

#: A feed status word that says when the page was re-read, which says nothing about the event.
NON_STATUS_VALUES: frozenset[str] = frozenset(
    {"updated", "update", "added", "new", "live", "latest", "draft", "true", "false", "1", "0"}
)

SEVERITY_CUES: tuple[SeverityCue, ...] = (
    # lives lost: the top band, and never softened by an adjacent "minor"
    SeverityCue("பலி", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 1.0,
                note="a life lost", count_kind=ObservationKind.FATALITIES),
    SeverityCue("பலியான*", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 1.0,
                note="lost their life", count_kind=ObservationKind.FATALITIES),
    SeverityCue("இறந்த*", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 1.0,
                note="died", count_kind=ObservationKind.FATALITIES),
    SeverityCue("இறப்பு", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="a death", count_kind=ObservationKind.FATALITIES),
    SeverityCue("உயிரிழந்த*", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 1.0,
                note="lost life", count_kind=ObservationKind.FATALITIES),
    SeverityCue("உயிரிழப்பு", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="loss of life", count_kind=ObservationKind.FATALITIES),
    SeverityCue("மூழ்கின*", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.9,
                note="they drowned, stated of people", count_kind=ObservationKind.FATALITIES),
    SeverityCue("மூழ்கியவர*", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.9,
                note="the person who drowned", count_kind=ObservationKind.FATALITIES),
    SeverityCue("பிணம்", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.85, note="a body recovered"),
    SeverityCue("killed", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 1.0,
                note="english: killed", count_kind=ObservationKind.FATALITIES),
    SeverityCue("died", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 1.0,
                note="english: died", count_kind=ObservationKind.FATALITIES),
    SeverityCue("death", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="english: death", count_kind=ObservationKind.FATALITIES),
    SeverityCue("deaths", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="english: deaths", count_kind=ObservationKind.FATALITIES),
    SeverityCue("fatal", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="english: fatal", count_kind=ObservationKind.FATALITIES),
    SeverityCue("fatality", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="english: fatality", count_kind=ObservationKind.FATALITIES),
    SeverityCue("fatalities", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.95,
                note="english: fatalities", count_kind=ObservationKind.FATALITIES),
    SeverityCue("perished", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.9,
                note="english: perished", count_kind=ObservationKind.FATALITIES),
    SeverityCue("drowned", SeverityCueCategory.FATALITY, SeverityLevel.CRITICAL, 0.9,
                note="english: drowned", count_kind=ObservationKind.FATALITIES),
    # injuries and harm to people
    SeverityCue("காயம்", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.8,
                note="an injury", count_kind=ObservationKind.INJURIES),
    SeverityCue("காயமடைந்த*", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.85,
                note="injured", count_kind=ObservationKind.INJURIES),
    SeverityCue("படுகாய*", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.85,
                note="seriously injured", count_kind=ObservationKind.INJURIES),
    SeverityCue("அடிபட்டு", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.7, note="struck and hurt"),
    SeverityCue("injured", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.85,
                note="english: injured", count_kind=ObservationKind.INJURIES),
    SeverityCue("injury", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.8,
                note="english: injury", count_kind=ObservationKind.INJURIES),
    SeverityCue("injuries", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.8,
                note="english: injuries", count_kind=ObservationKind.INJURIES),
    SeverityCue("hospitalised", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.75, note="english: admitted to hospital"),
    SeverityCue("hospitalized", SeverityCueCategory.INJURY, SeverityLevel.HIGH, 0.75, note="english: admitted to hospital"),
    # how many people the event reached
    SeverityCue("பாதிக்கப்பட்ட*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.6,
                 note="affected", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("பாதிப்பிற்கு*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.55,
                 note="subjected to harm", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("வெளியேற்ற*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.65,
                 note="evacuated", count_kind=ObservationKind.DISPLACED_PERSONS),
    SeverityCue("இடம்பெயர்*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.65,
                 note="displaced", count_kind=ObservationKind.DISPLACED_PERSONS),
    SeverityCue("நிவாரண முகாம*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.HIGH, 0.7,
                 note="relief camp stood up", count_kind=ObservationKind.DISPLACED_PERSONS),
    SeverityCue("பலர்", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.5, note="many people",
                count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("ஆயிரக்கணக்கான", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.HIGH, 0.7,
                 note="thousands of", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("நூற்றுக்கணக்கான", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.6,
                 note="hundreds of", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("லட்சக்கணக்கான", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.CRITICAL, 0.75,
                 note="lakhs of", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("affected", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.6,
                 note="english: affected", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("evacuat*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.65,
                 note="english: evacuated", count_kind=ObservationKind.DISPLACED_PERSONS),
    SeverityCue("displac*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.65,
                 note="english: displaced", count_kind=ObservationKind.DISPLACED_PERSONS),
    SeverityCue("stranded", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.MODERATE, 0.55,
                 note="english: stranded", count_kind=ObservationKind.AFFECTED_PERSONS),
    SeverityCue("relief camp", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.HIGH, 0.7,
                 note="english: relief camp", count_kind=ObservationKind.DISPLACED_PERSONS),
    # damage to property and land
    SeverityCue("சேதம்", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.55, note="damage"),
    SeverityCue("சேதமடைந்த*", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.6, note="was damaged"),
    SeverityCue("பெரும் சேதம்", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.75, note="large damage"),
    SeverityCue("மிகப்பெரிய சேதம்", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.8, note="very large damage"),
    SeverityCue("இழப்பு", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.55, note="loss"),
    SeverityCue("நஷ்டம்", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.55, note="crop or trade loss"),
    SeverityCue("நட்டம்", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.55, note="harm and loss"),
    SeverityCue("டிந்த*", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.7, note="gave way"),
    SeverityCue("சரிந்த*", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.7, note="collapsed"),
    SeverityCue("destroyed", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.75, note="english: destroyed"),
    SeverityCue("damage", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.55, note="english: damage"),
    SeverityCue("damaged", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.MODERATE, 0.6, note="english: damaged"),
    SeverityCue("major damage", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.8, note="english: major damage"),
    SeverityCue("collapsed", SeverityCueCategory.ECONOMIC_LOSS, SeverityLevel.HIGH, 0.7, note="english: collapsed"),
    # services and movement stopped
    SeverityCue("முடங்கிய*", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.HIGH, 0.7, note="paralysed"),
    SeverityCue("முடக்கப்பட்ட*", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.HIGH, 0.7, note="suspended"),
    SeverityCue("தடைபட்ட*", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.55, note="obstructed"),
    SeverityCue("மின்தடை", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.5,
                note="power cut", count_kind=ObservationKind.POWER_OUTAGE_DURATION),
    SeverityCue("நிறுத்தப்பட்ட*", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.5, note="stopped"),
    SeverityCue("தேங்கிய*", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.LOW, 0.5, note="water stood"),
    SeverityCue("paralysed", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.HIGH, 0.7, note="english: paralysed"),
    SeverityCue("paralyzed", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.HIGH, 0.7, note="english: paralyzed"),
    SeverityCue("power cut", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.5,
                note="english: power cut", count_kind=ObservationKind.POWER_OUTAGE_DURATION),
    SeverityCue("power outage", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.55,
                note="english: power outage", count_kind=ObservationKind.POWER_OUTAGE_DURATION),
    SeverityCue("outage", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.5,
                note="english: outage", count_kind=ObservationKind.POWER_OUTAGE_DURATION),
    SeverityCue("suspended", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.5, note="english: suspended"),
    SeverityCue("blocked", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.55, note="english: blocked"),
    SeverityCue("disrupted", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.6, note="english: disrupted"),
    SeverityCue("disruption", SeverityCueCategory.SERVICE_DISRUPTION, SeverityLevel.MODERATE, 0.55, note="english: disruption"),
    # the hazard itself, and its spread
    SeverityCue("கனமழை*", SeverityCueCategory.CONTINUING_HAZARD, SeverityLevel.LOW, 0.45, note="heavy rain, intensity stated"),
    SeverityCue("பெய்த*", SeverityCueCategory.CONTINUING_HAZARD, SeverityLevel.LOW, 0.35, note="rain fell"),
    SeverityCue("வெள்ளம்", SeverityCueCategory.CONTINUING_HAZARD, SeverityLevel.MODERATE, 0.55, note="flood water"),
    SeverityCue("வெள்ளப்பெருக்கு", SeverityCueCategory.CONTINUING_HAZARD, SeverityLevel.HIGH, 0.7, note="flood surge"),
    SeverityCue("பரவி*", SeverityCueCategory.ESCALATION_OR_SPREAD, SeverityLevel.MODERATE, 0.55, note="it spread"),
    SeverityCue("விரிதடைய*", SeverityCueCategory.ESCALATION_OR_SPREAD, SeverityLevel.HIGH, 0.7, note="widespread"),
    SeverityCue("தீவிரமடை*", SeverityCueCategory.ESCALATION_OR_SPREAD, SeverityLevel.HIGH, 0.7, note="it intensified"),
    SeverityCue("heavy rain", SeverityCueCategory.CONTINUING_HAZARD, SeverityLevel.LOW, 0.45, note="english: heavy rain"),
    SeverityCue("high flood", SeverityCueCategory.CONTINUING_HAZARD, SeverityLevel.HIGH, 0.7, note="english: high flood"),
    SeverityCue("spreading", SeverityCueCategory.ESCALATION_OR_SPREAD, SeverityLevel.MODERATE, 0.55, note="english: spreading"),
    SeverityCue("widespread", SeverityCueCategory.ESCALATION_OR_SPREAD, SeverityLevel.HIGH, 0.7, note="english: widespread"),
    # restriction of liberty and legal weight
    SeverityCue("கைது", SeverityCueCategory.LEGAL_OR_LIBERTY_RESTRICTION, SeverityLevel.MODERATE, 0.5, note="arrest"),
    SeverityCue("கட்டுப்பாடு", SeverityCueCategory.LEGAL_OR_LIBERTY_RESTRICTION, SeverityLevel.MODERATE, 0.5, note="a restriction imposed"),
    SeverityCue("தடையுத்தரவு", SeverityCueCategory.LEGAL_OR_LIBERTY_RESTRICTION, SeverityLevel.HIGH, 0.65, note="Section 144 order"),
    SeverityCue("arrested", SeverityCueCategory.LEGAL_OR_LIBERTY_RESTRICTION, SeverityLevel.MODERATE, 0.5, note="english: arrested"),
    SeverityCue("curfew", SeverityCueCategory.LEGAL_OR_LIBERTY_RESTRICTION, SeverityLevel.HIGH, 0.7, note="english: curfew"),
    # the response that a real event pulls out of the administration
    SeverityCue("மீட்புப் பணிகள்", SeverityCueCategory.RESPONSE_MOBILISED, SeverityLevel.MODERATE, 0.5, note="rescue work"),
    SeverityCue("தீயணைப்பு", SeverityCueCategory.RESPONSE_MOBILISED, SeverityLevel.MODERATE, 0.45, note="the fire service came"),
    SeverityCue("மீட்பு", SeverityCueCategory.RESPONSE_MOBILISED, SeverityLevel.LOW, 0.4, note="rescue"),
    SeverityCue("rescue", SeverityCueCategory.RESPONSE_MOBILISED, SeverityLevel.LOW, 0.4, note="english: rescue"),
    SeverityCue("fire brigade", SeverityCueCategory.RESPONSE_MOBILISED, SeverityLevel.MODERATE, 0.5, note="english: fire brigade"),
    SeverityCue("NDRF", SeverityCueCategory.RESPONSE_MOBILISED, SeverityLevel.HIGH, 0.7, note="national force tasked"),
    # public hardship, and the wording that keeps a claim small
    SeverityCue("சிரம*", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.LOW, 0.5, note="put about"),
    SeverityCue("inconvenience", SeverityCueCategory.AFFECTED_POPULATION, SeverityLevel.LOW, 0.5, note="english: inconvenience"),
    SeverityCue("minor", SeverityCueCategory.OTHER, SeverityLevel.LOW, 0.6, MITIGATING, note="english: stated as minor"),
    SeverityCue("limited", SeverityCueCategory.OTHER, SeverityLevel.LOW, 0.55, MITIGATING, note="english: limited"),
    SeverityCue("small", SeverityCueCategory.OTHER, SeverityLevel.LOW, 0.5, MITIGATING, note="english: small"),
    SeverityCue("isolated", SeverityCueCategory.OTHER, SeverityLevel.LOW, 0.5, MITIGATING, note="english: an isolated case"),
    SeverityCue("temporary", SeverityCueCategory.OTHER, SeverityLevel.LOW, 0.5, MITIGATING, note="english: temporary"),
)

#: Bands a stated count can carry a signal to. Read top down; the first threshold met wins.
MAGNITUDE_BANDS: dict[ObservationKind, tuple[tuple[int, SeverityLevel], ...]] = {
    ObservationKind.FATALITIES: ((1, SeverityLevel.CRITICAL),),
    ObservationKind.INJURIES: ((20, SeverityLevel.CRITICAL), (1, SeverityLevel.HIGH)),
    ObservationKind.DISPLACED_PERSONS: ((5000, SeverityLevel.CRITICAL), (500, SeverityLevel.HIGH), (1, SeverityLevel.MODERATE)),
    ObservationKind.AFFECTED_PERSONS: ((10000, SeverityLevel.CRITICAL), (1000, SeverityLevel.HIGH), (50, SeverityLevel.MODERATE), (1, SeverityLevel.LOW)),
    ObservationKind.AFFECTED_HOUSEHOLDS: ((2000, SeverityLevel.HIGH), (100, SeverityLevel.MODERATE), (1, SeverityLevel.LOW)),
    ObservationKind.AFFECTED_AREA_AGRICULTURE: ((10000, SeverityLevel.CRITICAL), (1000, SeverityLevel.HIGH), (100, SeverityLevel.MODERATE), (1, SeverityLevel.LOW)),
    ObservationKind.POWER_OUTAGE_DURATION: ((24, SeverityLevel.HIGH), (4, SeverityLevel.MODERATE), (1, SeverityLevel.LOW)),
    ObservationKind.WATER_LEVEL: ((6, SeverityLevel.HIGH), (3, SeverityLevel.MODERATE), (1, SeverityLevel.LOW)),
    ObservationKind.RAINFALL: ((200, SeverityLevel.CRITICAL), (100, SeverityLevel.HIGH), (50, SeverityLevel.MODERATE)),
    ObservationKind.PROTEST_PARTICIPANTS: ((1000, SeverityLevel.MODERATE), (100, SeverityLevel.LOW)),
    ObservationKind.DETENTIONS: ((10, SeverityLevel.MODERATE), (1, SeverityLevel.LOW)),
}

# fmt: off
COUNT_HEADS: tuple[CountHead, ...] = (
    CountHead("பேர*", ObservationKind.COUNT_GENERIC, "people", "persons counted"),
    CountHead("பேரணி*", ObservationKind.COUNT_GENERIC, "people", "groups of people"),
    CountHead("நபர*", ObservationKind.COUNT_GENERIC, "people", "a person"),
    CountHead("மக்கள*", ObservationKind.COUNT_GENERIC, "people", "people"),
    CountHead("குடும்ப*", ObservationKind.AFFECTED_HOUSEHOLDS, "families", "households counted"),
    CountHead("வீடு*", ObservationKind.BUILDING_COUNT, "houses", "houses counted"),
    CountHead("கிராமம்*", ObservationKind.AREA_COVERED, "villages", "villages counted"),
    CountHead("ஏக்கர*", ObservationKind.AFFECTED_AREA_AGRICULTURE, "acres", "land extent"),
    CountHead("மணி நேர*", ObservationKind.POWER_OUTAGE_DURATION, "hours", "a stretch of hours"),
    CountHead("மணிநேர*", ObservationKind.POWER_OUTAGE_DURATION, "hours", "a stretch of hours"),
    CountHead("நேர*", ObservationKind.POWER_OUTAGE_DURATION, "hours", "time for which it went on"),
    CountHead("அடி*", ObservationKind.WATER_LEVEL, "feet", "a water height"),
    CountHead("மில்லிமீட்டர*", ObservationKind.RAINFALL, "mm", "rainfall depth"),
    CountHead("mm", ObservationKind.RAINFALL, "mm", "english: rainfall depth"),
    CountHead("centimetre", ObservationKind.RAINFALL, "cm", "english: rainfall depth"),
    CountHead("acre", ObservationKind.AFFECTED_AREA_AGRICULTURE, "acres", "english: land extent"),
    CountHead("acres", ObservationKind.AFFECTED_AREA_AGRICULTURE, "acres", "english: land extent"),
    CountHead("hectare", ObservationKind.AFFECTED_AREA_AGRICULTURE, "hectares", "english: land extent"),
    CountHead("hectares", ObservationKind.AFFECTED_AREA_AGRICULTURE, "hectares", "english: land extent"),
    CountHead("hour", ObservationKind.POWER_OUTAGE_DURATION, "hours", "english: hours"),
    CountHead("hours", ObservationKind.POWER_OUTAGE_DURATION, "hours", "english: hours"),
    CountHead("feet", ObservationKind.WATER_LEVEL, "feet", "english: a water height"),
    CountHead("villages", ObservationKind.AREA_COVERED, "villages", "english: villages counted"),
    CountHead("wards", ObservationKind.AREA_COVERED, "wards", "english: wards counted"),
    CountHead("families", ObservationKind.AFFECTED_HOUSEHOLDS, "families", "english: households counted"),
    CountHead("households", ObservationKind.AFFECTED_HOUSEHOLDS, "families", "english: households counted"),
    CountHead("people", ObservationKind.COUNT_GENERIC, "people", "english: persons counted"),
    CountHead("persons", ObservationKind.COUNT_GENERIC, "people", "english: persons counted"),
    CountHead("passengers", ObservationKind.COUNT_GENERIC, "people", "english: passengers counted"),
    CountHead("students", ObservationKind.COUNT_GENERIC, "people", "english: students counted"),
    CountHead("houses", ObservationKind.BUILDING_COUNT, "houses", "english: houses counted"),
    CountHead("buildings", ObservationKind.BUILDING_COUNT, "houses", "english: buildings counted"),
    CountHead("shops", ObservationKind.BUILDING_COUNT, "shops", "english: shops counted"),
    CountHead("detained", ObservationKind.DETENTIONS, "people", "english: people detained"),
)# fmt: on

#: A source's own severity scale, quoted from its ``severity`` field rather than inferred.
AUTHORITATIVE_SEVERITY: dict[str, SeverityLevel] = {
    "red alert": SeverityLevel.CRITICAL,
    "orange alert": SeverityLevel.HIGH,
    "yellow alert": SeverityLevel.MODERATE,
    "blue alert": SeverityLevel.LOW,
    "extremely high warning": SeverityLevel.CRITICAL,
    "very severe": SeverityLevel.CRITICAL,
    "severe": SeverityLevel.HIGH,
}

#: Scale names that are not a severity at all, so they must never be read as one.
NON_SEVERITY_VALUES: frozenset[str] = frozenset(
    {"normally distributed", "nil", "none", "n/a", "na", "unspecified", "not available"}
)


def key_of(surface: str) -> tuple[str, ...]:
    """The word keys a surface is matched against, with the stem flag kept visible."""
    return tuple(part for part in surface.strip().split())


def is_stem(part: str) -> bool:
    return part.endswith(STEM_MARKER)


def stem_of(part: str) -> str:
    return part[: -len(STEM_MARKER)] if is_stem(part) else part


def status_cues_for(state: OperationalState) -> tuple[StatusCue, ...]:
    return tuple(cue for cue in STATUS_CUES if cue.state is state)


def severity_cues_for(category: SeverityCueCategory) -> tuple[SeverityCue, ...]:
    return tuple(cue for cue in SEVERITY_CUES if cue.category is category)


def band_for_count(kind: ObservationKind, value: float) -> Optional[SeverityLevel]:
    """The band a stated count reaches for this kind, or None when the kind claims no scale."""
    for floor, band in MAGNITUDE_BANDS.get(kind, ()):
        if value >= floor:
            return band
    return None


def declared_status(value: str) -> Optional[OperationalState]:
    """What the record's own status field says, or None when it says nothing useful."""
    key = " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())
    if key in NON_STATUS_VALUES:
        return None
    return DECLARED_STATUS_VALUES.get(key)


def declared_severity(value: str) -> Optional[SeverityLevel]:
    """A severity the source stated on its own official scale, or None."""
    key = " ".join(value.strip().casefold().replace("-", " ").split())
    if key in NON_SEVERITY_VALUES:
        return None
    return AUTHORITATIVE_SEVERITY.get(key)


def operations_integrity_errors() -> list[str]:
    """Report drift between this vocabulary and the contracts it feeds. Empty list is clean."""
    from intelligence.extraction.mention_text import fold

    errors: list[str] = []
    states = set(OperationalState)
    categories = set(SeverityCueCategory)
    levels = set(SeverityLevel)
    kinds = set(ObservationKind)

    status_claims: dict[str, set[tuple[str, ...]]] = {}
    for cue in STATUS_CUES:
        where = f"status cue {cue.surface!r} -> {cue.state.value}"
        if not cue.surface.strip() or cue.surface != cue.surface.strip():
            errors.append(f"{where}: surface is blank or carries surrounding whitespace")
        if cue.surface.count(STEM_MARKER) > 1 or (
            STEM_MARKER in cue.surface and not cue.surface.endswith(STEM_MARKER)
        ):
            errors.append(f"{where}: the stem marker belongs at the end of one word only")
        if cue.state not in states or cue.state is OperationalState.UNKNOWN:
            errors.append(f"{where} is not a condition a source can state")
        if not cue.note:
            errors.append(f"{where} carries no gloss for the next reader")
        parts = key_of(cue.surface)
        if not parts:
            errors.append(f"{where} names no words to match")
        if any(not part.strip(STEM_MARKER) for part in parts):
            errors.append(f"{where} has a bare stem marker")
        claimed = {fold(cue.surface), *(fold(spelling) for spelling in cue.latin)}
        for key in claimed:
            status_claims.setdefault(key, set()).add(cue.claim)
        if cue.state is OperationalState.UNKNOWN:
            errors.append(f"{where} asserts nothing")

    for key, claims in sorted(status_claims.items()):
        if len(claims) > 1:
            errors.append(
                "status key "
                + repr(key)
                + " claims "
                + ", ".join(sorted(" ".join(claim) for claim in claims))
            )

    severity_claims: dict[str, set[tuple[str, ...]]] = {}
    for cue in SEVERITY_CUES:
        where = f"severity cue {cue.surface!r} -> {cue.category.value}"
        if not cue.surface.strip() or cue.surface != cue.surface.strip():
            errors.append(f"{where}: surface is blank or carries surrounding whitespace")
        if cue.surface.count(STEM_MARKER) > 1 or (
            STEM_MARKER in cue.surface and not cue.surface.endswith(STEM_MARKER)
        ):
            errors.append(f"{where}: the stem marker belongs at the end of one word only")
        if cue.category not in categories or cue.category is SeverityCueCategory.UNRESOLVED:
            errors.append(f"{where} is not a cue category that can carry evidence")
        if cue.band not in levels or cue.band in (
            SeverityLevel.UNRESOLVED,
            SeverityLevel.INFO,
        ):
            errors.append(f"{where} claims {cue.band.value}, which is not a severity band")
        if not 0.0 < cue.weight <= 1.0:
            errors.append(f"{where} weight {cue.weight} is not a fraction above zero")
        if cue.direction not in DIRECTIONS:
            errors.append(f"{where} direction {cue.direction!r} is not one of {DIRECTIONS}")
        if cue.direction is MITIGATING and cue.band is not SeverityLevel.LOW:
            errors.append(f"{where}: a mitigating cue can only argue for the lowest band")
        if not cue.note:
            errors.append(f"{where} carries no gloss for the next reader")
        if cue.count_kind is not None and cue.count_kind not in kinds:
            errors.append(f"{where} counts {cue.count_kind}, which is no observation kind")
        claimed = {fold(cue.surface), *(fold(spelling) for spelling in cue.latin)}
        for key in claimed:
            severity_claims.setdefault(key, set()).add(cue.claim)

    for key, claims in sorted(severity_claims.items()):
        if len(claims) > 1:
            errors.append(
                "severity key "
                + repr(key)
                + " claims "
                + ", ".join(sorted(" ".join(claim) for claim in claims))
            )

    for kind, ladder in MAGNITUDE_BANDS.items():
        if kind not in kinds or kind in (ObservationKind.UNRESOLVED, ObservationKind.OTHER):
            errors.append(f"magnitude ladder for {kind} names no observable quantity")
        if not ladder:
            errors.append(f"magnitude ladder for {kind.value} is empty")
        floors = [floor for floor, _ in ladder]
        if floors != sorted(floors, reverse=True):
            errors.append(f"magnitude ladder for {kind.value} must run strongest first")
        for floor, band in ladder:
            if floor < 1:
                errors.append(f"{kind.value} threshold {floor} counts nothing")
            if band not in levels or band in (
                SeverityLevel.UNRESOLVED,
                SeverityLevel.INFO,
            ):
                errors.append(f"{kind.value} threshold {floor} claims {band.value}")

    head_kinds: dict[str, ObservationKind] = {}
    for head in COUNT_HEADS:
        where = f"count head {head.surface!r}"
        if head.kind not in kinds or head.kind in (
            ObservationKind.UNRESOLVED,
            ObservationKind.DATE_REFERENCE,
            ObservationKind.AGE,
        ):
            errors.append(f"{where} counts {head.kind.value}, which no quantity answers to")
        if not head.surface.strip() or head.surface != head.surface.strip():
            errors.append(f"{where} is blank or carries surrounding whitespace")
        if not head.unit:
            errors.append(f"{where} states no unit for the reader")
        key = fold(head.surface)
        if key in head_kinds and head_kinds[key] is not head.kind:
            errors.append(f"{where} counts the same head as {head_kinds[key].value}")
        head_kinds[key] = head.kind

    for value, state in DECLARED_STATUS_VALUES.items():
        if state is OperationalState.UNKNOWN:
            errors.append(f"declared status {value!r} maps to unknown")
        if value != value.strip() or value != value.casefold():
            errors.append(f"declared status key {value!r} is not a plain lowercase key")
    clash = set(DECLARED_STATUS_VALUES) & set(NON_STATUS_VALUES)
    if clash:
        errors.append("status words are both a condition and a re-fetch marker: " + ", ".join(sorted(clash)))

    for value, band in AUTHORITATIVE_SEVERITY.items():
        if value != value.strip() or value != value.casefold():
            errors.append(f"severity scale key {value!r} is not a plain lowercase key")
        if band not in levels or band is SeverityLevel.UNRESOLVED:
            errors.append(f"severity scale {value!r} claims {band.value}")
    clash = set(AUTHORITATIVE_SEVERITY) & set(NON_SEVERITY_VALUES)
    if clash:
        errors.append("severity values are both a scale and no severity: " + ", ".join(sorted(clash)))

    return errors


__all__ = [
    "ACCEPTANCE_FLOOR",
    "AUTHORITATIVE_SEVERITY",
    "COUNT_HEADS",
    "CountHead",
    "DECLARED_STATUS_VALUES",
    "DIRECTIONS",
    "ESCALATING",
    "MITIGATING",
    "MAGNITUDE_BANDS",
    "NEGATION_SUBSTRINGS",
    "NEGATION_WINDOW",
    "NEGATION_WORDS",
    "NON_SEVERITY_VALUES",
    "NON_STATUS_VALUES",
    "OPERATIONS_LEXICON_VERSION",
    "SEVERITY_CUES",
    "STEM_MARKER",
    "STATUS_CUES",
    "SeverityCue",
    "StatusCue",
    "band_for_count",
    "declared_severity",
    "declared_status",
    "is_stem",
    "key_of",
    "operations_integrity_errors",
    "severity_cues_for",
    "stem_of",
    "status_cues_for",
]
