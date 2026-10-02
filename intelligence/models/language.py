"""Text, language and translation contracts.

Invariant: the source text is canonical and is never replaced. Translation,
normalisation and transliteration are additional, separately-labelled
representations that keep the original recoverable.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, model_validator

from intelligence.models.base import Confidence, OptionalConfidence, StrictModel
from intelligence.models.enums import ExtractionMethod, SummaryKind, TextRole
from intelligence.models.enums import ScriptType

#: ISO 639-2 "und" - undetermined. Detection failure is recorded, not guessed.
UNKNOWN_LANGUAGE = "und"
#: ISO 639-2 "mul" - genuinely multilingual beyond a single primary language.
MULTILINGUAL = "mul"


class TextRepresentation(StrictModel):
    """One labelled representation of the same underlying text."""

    representation_id: str
    text: str
    role: TextRole
    language: str = Field(default=UNKNOWN_LANGUAGE, min_length=2, max_length=3)
    script: ScriptType = ScriptType.UNKNOWN

    #: Which role this was derived from. Mandatory for derived roles so a
    #: translation can always be traced back to the representation it came from.
    derived_from: Optional[str] = None
    provider: Optional[str] = None
    provider_version: Optional[str] = None

    #: False when offsets are meaningless (e.g. OCR of an image) or when the
    #: provider did not return alignment information.
    offsets_align_with_source: bool = True

    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_derivation(self) -> "TextRepresentation":
        if self.role is TextRole.SOURCE:
            if self.derived_from is not None:
                raise ValueError("a SOURCE representation cannot be derived from anything")
            if self.method is ExtractionMethod.UNRESOLVED:
                raise ValueError("a SOURCE representation must declare how it was obtained")
            return self

        if self.derived_from is None:
            raise ValueError(f"a {self.role.value} representation must declare derived_from")

        if self.role is TextRole.TRANSLATION:
            if not self.offsets_align_with_source:
                if self.confidence is None:
                    raise ValueError(
                        "a translation whose offsets do not align with the source "
                        "must carry confidence"
                    )
            if self.provider is None:
                raise ValueError("a translation must name its provider")
        return self


class LanguageDetection(StrictModel):
    """How the module decided the language - never the ingestion constant."""

    primary_language: str = Field(default=UNKNOWN_LANGUAGE, min_length=2, max_length=3)
    secondary_language: Optional[str] = Field(default=None, min_length=2, max_length=3)
    script_ratios: dict[str, Confidence] = Field(default_factory=dict)
    tamil_script_ratio: Optional[Confidence] = None
    latin_script_ratio: Optional[Confidence] = None
    detected_by_record: dict[str, str] = Field(default_factory=dict)
    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_method_when_decided(self) -> "LanguageDetection":
        if self.primary_language != UNKNOWN_LANGUAGE:
            if self.method is ExtractionMethod.UNRESOLVED:
                raise ValueError("a resolved language requires a detection method")
            if self.confidence is None:
                raise ValueError("a resolved language requires detection confidence")
        return self


class LanguageInfo(StrictModel):
    """All representations of an incident's text, plus how language was decided."""

    primary_language: str = Field(default=UNKNOWN_LANGUAGE, min_length=2, max_length=3)
    primary_script: ScriptType = ScriptType.UNKNOWN
    is_multilingual: bool = False
    detection: Optional[LanguageDetection] = None
    text_representations: list[TextRepresentation] = Field(default_factory=list)
    #: Raw ``data.language`` value inherited from the record, kept for audit.
    #: Intelligence must detect for itself rather than trust this.
    inherited_language_hint: Optional[str] = None

    @model_validator(mode="after")
    def _source_text_is_preserved(self) -> "LanguageInfo":
        representation_ids = [r.representation_id for r in self.text_representations]
        if len(set(representation_ids)) != len(representation_ids):
            raise ValueError("text representation ids must be unique")

        sources = [
            r for r in self.text_representations if r.role is TextRole.SOURCE
        ]
        if self.text_representations and not sources:
            raise ValueError(
                "text_representations must include at least one SOURCE role; "
                "the original text may never be discarded"
            )

        for rep in self.text_representations:
            if rep.derived_from is not None and rep.derived_from not in representation_ids:
                raise ValueError(
                    f"representation {rep.representation_id} derives from unknown "
                    f"representation {rep.derived_from!r}"
                )
            if rep.role is TextRole.TRANSLATION and sources:
                if any(rep.language == s.language for s in sources):
                    raise ValueError(
                        "a TRANSLATION must be in a different language than its SOURCE"
                    )
        return self

    @model_validator(mode="after")
    def _language_claim_is_grounded(self) -> "LanguageInfo":
        """``primary_language`` summarises ``detection``; it may not replace it.

        Ingestion hardcodes ``data.language``, so a language stated with no
        detection record is indistinguishable from that constant being copied
        through. ``und`` stays legal: declining to decide is a real state.
        """
        if self.primary_language != UNKNOWN_LANGUAGE and self.detection is None:
            raise ValueError(
                "a resolved primary_language requires a detection record stating "
                "how the language was derived"
            )
        if (
            self.detection is not None
            and self.detection.primary_language != UNKNOWN_LANGUAGE
            and self.detection.primary_language != self.primary_language
        ):
            raise ValueError(
                f"primary_language {self.primary_language!r} disagrees with "
                f"detection.primary_language {self.detection.primary_language!r}"
            )
        return self

    def source_texts(self) -> list[TextRepresentation]:
        return [r for r in self.text_representations if r.role is TextRole.SOURCE]

    def translation_texts(self) -> list[TextRepresentation]:
        return [r for r in self.text_representations if r.role is TextRole.TRANSLATION]


class LocalizedText(StrictModel):
    """A short text slot that can hold a source version and a display version."""

    source: Optional[str] = None
    translated: Optional[str] = None
    language: str = Field(default=UNKNOWN_LANGUAGE, min_length=2, max_length=3)
    translated_language: Optional[str] = Field(default=None, min_length=2, max_length=3)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _never_replace_source(self) -> "LocalizedText":
        if self.translated is not None:
            if self.source is None:
                raise ValueError(
                    "a translated value requires the source value too; "
                    "translation supplements the original, it never replaces it"
                )
            if self.translated_language is None:
                raise ValueError("a translated value requires translated_language")
            if self.translated_language == self.language:
                raise ValueError("translated_language must differ from language")
        if self.translated_language is not None and self.translated is None:
            raise ValueError("translated_language set without a translated value")
        return self


class TitleInfo(StrictModel):
    """Headline as published, plus an optional display translation."""

    text: LocalizedText = Field(default_factory=LocalizedText)
    #: Headlines are copied verbatim from CommonRecord.title, so this is a
    #: source fact rather than an extraction - stated explicitly to avoid the
    #: reader assuming the headline was generated by the pipeline.
    is_verbatim_from_record: bool = True
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)


class SummaryInfo(StrictModel):
    """A summary is inferred text, so its construction is declared."""

    text: LocalizedText = Field(default_factory=LocalizedText)
    kind: SummaryKind = SummaryKind.UNRESOLVED
    method: ExtractionMethod = ExtractionMethod.UNRESOLVED
    #: An abstractive or LLM summary must never be presented as source wording.
    is_verbatim_from_record: bool = False
    confidence: OptionalConfidence = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _abstractive_needs_confidence(self) -> "SummaryInfo":
        if self.kind in (SummaryKind.ABSTRACTIVE, SummaryKind.STRUCTURED):
            if self.confidence is None:
                raise ValueError(f"a {self.kind.value} summary requires confidence")
            if self.method is ExtractionMethod.UNRESOLVED:
                raise ValueError(f"a {self.kind.value} summary requires a method")
        return self
