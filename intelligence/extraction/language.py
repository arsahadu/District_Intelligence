"""Language detection from script evidence over code points."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from intelligence.extraction.spans import SourceField
from intelligence.models.enums import ExtractionMethod, ScriptType, TextRole
from intelligence.models.language import (
    MULTILINGUAL,
    UNKNOWN_LANGUAGE,
    LanguageDetection,
    LanguageInfo,
    TextRepresentation,
)

TAMIL_LANGUAGE = "ta"
ENGLISH_LANGUAGE = "en"

TAMIL_BLOCK = range(0x0B80, 0x0C00)
LATIN_BLOCKS = (
    range(0x0041, 0x005B),
    range(0x0061, 0x007B),
    range(0x00C0, 0x0250),
    range(0x1E00, 0x1EFF),
)
SCRIPT_BUCKETS = ("tamil", "latin", "other")

#: Share of letters a script must hold for the text to be called that language.
DOMINANT_SCRIPT_RATIO = 0.8
#: Share at which a second script counts as a second language being present.
PRESENT_SCRIPT_RATIO = 0.1

#: Latin script serves many languages, so ``en`` requires English function words.
ENGLISH_FUNCTION_WORDS = frozenset(
    """
    a an the and or but if so than then that this these those there here of in on at to from
    by with for about into over under during while before after per up down out off is are was
    were be been being have has had will would can could should may might must not no yes as
    it its he him his she her they them their we us our you your i me my who whom whose which
    what when where why how also more most such very said says say told tell given give within
    without against among between according since until though however therefore
    """.split()
)
LATIN_WORD = re.compile(r"[a-zA-Z]+")

LANGUAGE_ALIASES = {
    "tamil": TAMIL_LANGUAGE,
    "ta": TAMIL_LANGUAGE,
    "english": ENGLISH_LANGUAGE,
    "en": ENGLISH_LANGUAGE,
    "und": UNKNOWN_LANGUAGE,
    "mul": MULTILINGUAL,
}


def script_bucket(character: str) -> Optional[str]:
    """Which script evidence a letter carries; ``None`` for marks, digits, punctuation."""
    if not unicodedata.category(character).startswith("L"):
        return None
    code = ord(character)
    if code in TAMIL_BLOCK:
        return "tamil"
    if any(code in block for block in LATIN_BLOCKS):
        return "latin"
    return "other"


@dataclass(frozen=True)
class ScriptProfile:
    """Letter counts by script. Marks, digits and punctuation carry no evidence."""

    tamil: int = 0
    latin: int = 0
    other: int = 0

    @property
    def letters(self) -> int:
        return self.tamil + self.latin + self.other

    def ratio(self, bucket: str) -> float:
        if not self.letters:
            return 0.0
        return round(getattr(self, bucket) / self.letters, 4)

    @property
    def tamil_ratio(self) -> float:
        return self.ratio("tamil")

    @property
    def latin_ratio(self) -> float:
        return self.ratio("latin")

    @property
    def other_ratio(self) -> float:
        return self.ratio("other")

    @property
    def script_type(self) -> ScriptType:
        if not self.letters:
            return ScriptType.UNKNOWN
        if self.other_ratio >= DOMINANT_SCRIPT_RATIO:
            return ScriptType.OTHER
        tamil, latin = self.tamil_ratio, self.latin_ratio
        if tamil >= DOMINANT_SCRIPT_RATIO:
            return ScriptType.TAMIL
        if latin >= DOMINANT_SCRIPT_RATIO:
            return ScriptType.LATIN
        if tamil >= PRESENT_SCRIPT_RATIO and latin >= PRESENT_SCRIPT_RATIO:
            return ScriptType.MIXED
        return ScriptType.TAMIL if tamil > latin else ScriptType.LATIN

    def ratios(self) -> dict[str, float]:
        return {bucket: self.ratio(bucket) for bucket in SCRIPT_BUCKETS if getattr(self, bucket)}


def profile(text: str) -> ScriptProfile:
    counts = {"tamil": 0, "latin": 0, "other": 0}
    for character in text:
        bucket = script_bucket(character)
        if bucket:
            counts[bucket] += 1
    return ScriptProfile(**counts)


def english_vocabulary_found(text: str) -> bool:
    return any(word.lower() in ENGLISH_FUNCTION_WORDS for word in LATIN_WORD.findall(text))


@dataclass(frozen=True)
class LanguageAssessment:
    """A language claim plus the script evidence and the reasoning behind it."""

    profile: ScriptProfile
    primary_language: str
    secondary_language: Optional[str]
    is_multilingual: bool
    english_vocabulary_found: bool
    reason: str

    @property
    def is_declared(self) -> bool:
        return self.primary_language != UNKNOWN_LANGUAGE

    @property
    def confidence(self) -> Optional[float]:
        if not self.is_declared:
            return None
        if self.primary_language == MULTILINGUAL:
            return round(1.0 - abs(self.profile.tamil_ratio - self.profile.latin_ratio), 4)
        if self.primary_language == TAMIL_LANGUAGE:
            return self.profile.tamil_ratio
        return self.profile.latin_ratio

    def detection(
        self,
        *,
        detected_by_record: Optional[dict[str, str]] = None,
        evidence_ids: Iterable[str] = (),
    ) -> LanguageDetection:
        return LanguageDetection(
            primary_language=self.primary_language,
            secondary_language=self.secondary_language,
            script_ratios=self.profile.ratios(),
            tamil_script_ratio=self.profile.tamil_ratio if self.profile.letters else None,
            latin_script_ratio=self.profile.latin_ratio if self.profile.letters else None,
            detected_by_record=dict(detected_by_record or {}),
            method=ExtractionMethod.RULE,
            confidence=self.confidence,
            evidence_ids=list(evidence_ids),
        )


def assess(text: str) -> LanguageAssessment:
    """Name a language from letter shapes, declining when they do not say one."""
    script = profile(text)
    if not script.letters:
        return LanguageAssessment(
            script, UNKNOWN_LANGUAGE, None, False, False, "no letters to judge"
        )

    tamil, latin, other = script.tamil_ratio, script.latin_ratio, script.other_ratio
    english = latin >= PRESENT_SCRIPT_RATIO and english_vocabulary_found(text)

    if other >= DOMINANT_SCRIPT_RATIO:
        return LanguageAssessment(
            script,
            UNKNOWN_LANGUAGE,
            None,
            False,
            False,
            "letters belong to neither the Tamil nor the Latin script",
        )

    if tamil >= DOMINANT_SCRIPT_RATIO:
        return LanguageAssessment(
            script,
            TAMIL_LANGUAGE,
            ENGLISH_LANGUAGE if english else None,
            english,
            english,
            f"{tamil:.0%} of letters are Tamil",
        )

    if latin >= DOMINANT_SCRIPT_RATIO:
        if not english:
            return LanguageAssessment(
                script,
                UNKNOWN_LANGUAGE,
                None,
                False,
                False,
                "Latin script without English function words",
            )
        return LanguageAssessment(
            script,
            ENGLISH_LANGUAGE,
            TAMIL_LANGUAGE if tamil >= PRESENT_SCRIPT_RATIO else None,
            tamil >= PRESENT_SCRIPT_RATIO,
            english,
            f"{latin:.0%} of letters are Latin",
        )

    if tamil >= PRESENT_SCRIPT_RATIO and latin >= PRESENT_SCRIPT_RATIO and english:
        return LanguageAssessment(
            script,
            MULTILINGUAL,
            None,
            True,
            english,
            f"Tamil {tamil:.0%} and Latin {latin:.0%} of letters, neither dominant",
        )

    if tamil >= PRESENT_SCRIPT_RATIO:
        return LanguageAssessment(
            script,
            TAMIL_LANGUAGE,
            None,
            False,
            english,
            f"Tamil is the only nameable language at {tamil:.0%}",
        )

    return LanguageAssessment(
        script,
        UNKNOWN_LANGUAGE,
        None,
        False,
        english,
        f"no script reaches {DOMINANT_SCRIPT_RATIO:.0%} of letters",
    )


def representation_id(source: SourceField) -> str:
    return f"rep-{source.record_id}-{source.field.replace('.', '-')}"


def source_representation(
    source: SourceField,
    *,
    representation_id_override: Optional[str] = None,
    evidence_ids: Iterable[str] = (),
    assessment: Optional[LanguageAssessment] = None,
) -> TextRepresentation:
    """The field verbatim as a SOURCE representation."""
    assessment = assessment or assess(source.text)
    return TextRepresentation(
        representation_id=representation_id_override or representation_id(source),
        text=source.text,
        role=TextRole.SOURCE,
        language=assessment.primary_language,
        script=assessment.profile.script_type,
        method=ExtractionMethod.SOURCE_METADATA,
        evidence_ids=list(evidence_ids),
    )


def _aggregate(sources: Sequence[SourceField]) -> tuple[LanguageAssessment, dict[str, str]]:
    if not sources:
        raise ValueError("language detection needs at least one source field")
    by_record: dict[str, str] = {}
    letters_by_record: dict[str, int] = {}
    for source in sources:
        field_assessment = assess(source.text)
        if field_assessment.profile.letters >= letters_by_record.get(source.record_id, 0):
            by_record[source.record_id] = field_assessment.primary_language
            letters_by_record[source.record_id] = field_assessment.profile.letters
    return assess("".join(source.text for source in sources)), by_record


def detection(
    sources: Sequence[SourceField],
    *,
    evidence_ids: Iterable[str] = (),
) -> LanguageDetection:
    """One detection over several fields, noting how each record read."""
    assessment, by_record = _aggregate(sources)
    return assessment.detection(detected_by_record=by_record, evidence_ids=evidence_ids)


def language_info(
    sources: Sequence[SourceField],
    *,
    derived_representations: Iterable[TextRepresentation] = (),
    inherited_hint: Optional[str] = None,
    source_evidence_ids: Iterable[str] = (),
    detection_evidence_ids: Iterable[str] = (),
) -> LanguageInfo:
    """Assemble the Stage 1 ``LanguageInfo`` contract from real source fields."""
    assessment, by_record = _aggregate(sources)
    derived = list(derived_representations)
    if any(rep.role is TextRole.SOURCE for rep in derived):
        raise ValueError(
            "SOURCE representations are made from the fields; derived_representations "
            "holds only text derived from them"
        )
    representations = [
        source_representation(source, evidence_ids=source_evidence_ids) for source in sources
    ]
    representations.extend(derived)

    return LanguageInfo(
        primary_language=assessment.primary_language,
        primary_script=assessment.profile.script_type,
        is_multilingual=assessment.is_multilingual,
        detection=assessment.detection(
            detected_by_record=by_record, evidence_ids=detection_evidence_ids
        ),
        text_representations=representations,
        inherited_language_hint=inherited_hint,
    )


def normalise_hint(hint: Optional[str]) -> Optional[str]:
    """Map a feed's language label to the code the contracts store."""
    if hint is None:
        return None
    primary = hint.strip().lower().split("-")[0]
    if not primary:
        return None
    return LANGUAGE_ALIASES.get(primary, primary[:3])


def hint_conflict(info: LanguageInfo) -> Optional[str]:
    """Where the feed's own label disagrees with what the text says."""
    hint = normalise_hint(info.inherited_language_hint)
    if hint is None or hint == info.primary_language:
        return None
    return (
        f"inherited language hint {info.inherited_language_hint!r} does not match the "
        f"detected primary language {info.primary_language!r}"
    )
