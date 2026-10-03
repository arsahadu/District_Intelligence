"""Language handling contracts: the Tamil original is never discarded."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intelligence.models.enums import ExtractionMethod, ScriptType, TextRole
from intelligence.models.language import (
    UNKNOWN_LANGUAGE,
    LanguageDetection,
    LanguageInfo,
    LocalizedText,
    SummaryInfo,
    TextRepresentation,
    TitleInfo,
)
from intelligence.tests.builders import CONTENT_TA, TITLE_TA, protest_language


def ta_detection(**overrides) -> LanguageDetection:
    """A minimal grounded detection, for tests about something else entirely."""
    payload = {
        "primary_language": "ta",
        "method": ExtractionMethod.RULE,
        "confidence": 0.98,
    }
    payload.update(overrides)
    return LanguageDetection(**payload)


def source_rep(text: str = CONTENT_TA, **overrides) -> TextRepresentation:
    payload = {
        "representation_id": "rep-source",
        "text": text,
        "role": TextRole.SOURCE,
        "language": "ta",
        "script": ScriptType.TAMIL,
        "method": ExtractionMethod.SOURCE_METADATA,
    }
    payload.update(overrides)
    return TextRepresentation(**payload)


def translation_rep(**overrides) -> TextRepresentation:
    payload = {
        "representation_id": "rep-en",
        "text": "Sanitation workers staged a protest at the corporation office.",
        "role": TextRole.TRANSLATION,
        "language": "en",
        "script": ScriptType.LATIN,
        "derived_from": "rep-source",
        "provider": "placeholder-translation-service",
        "method": ExtractionMethod.LLM,
        "confidence": 0.7,
        "offsets_align_with_source": False,
    }
    payload.update(overrides)
    return TextRepresentation(**payload)


def test_tamil_source_text_is_stored_verbatim():
    info = LanguageInfo(
        primary_language="ta",
        primary_script=ScriptType.TAMIL,
        detection=ta_detection(),
        text_representations=[source_rep()],
    )

    assert info.source_texts()[0].text == CONTENT_TA
    assert "ஆர்ப்பாட்டத்தில்" in info.source_texts()[0].text


def test_translation_is_added_beside_the_source_not_instead_of_it():
    info = LanguageInfo(
        primary_language="ta",
        detection=ta_detection(),
        text_representations=[source_rep(), translation_rep()],
    )

    assert info.source_texts()[0].text == CONTENT_TA
    assert len(info.translation_texts()) == 1
    assert info.translation_texts()[0].text != CONTENT_TA
    assert info.translation_texts()[0].derived_from == "rep-source"


def test_a_representation_set_with_no_source_text_is_rejected():
    """The failure mode this guards against is 'we translated and dropped Tamil'."""
    with pytest.raises(ValidationError, match="may never be discarded"):
        LanguageInfo(
            primary_language="en",
            text_representations=[
                translation_rep().model_copy(update={"representation_id": "rep-x"})
            ],
        )


def test_translation_into_the_same_language_is_rejected():
    with pytest.raises(ValidationError, match="different language"):
        LanguageInfo(
            primary_language="ta",
            text_representations=[source_rep(), translation_rep(language="ta")],
        )


def test_derived_representations_must_name_their_parent():
    with pytest.raises(ValidationError, match="must declare derived_from"):
        translation_rep(derived_from=None)


def test_derived_representations_cannot_reference_a_missing_parent():
    with pytest.raises(ValidationError, match="unknown representation"):
        LanguageInfo(
            primary_language="ta",
            text_representations=[source_rep(), translation_rep(derived_from="rep-absent")],
        )


def test_source_representation_must_declare_how_it_was_obtained():
    with pytest.raises(ValidationError, match="how it was obtained"):
        source_rep(method=ExtractionMethod.UNRESOLVED)


def test_translation_without_provider_is_rejected():
    with pytest.raises(ValidationError, match="must name its provider"):
        translation_rep(provider=None)


def test_undetermined_language_is_a_valid_state_not_a_guess():
    info = LanguageInfo(
        primary_language=UNKNOWN_LANGUAGE,
        text_representations=[
            source_rep(
                language=UNKNOWN_LANGUAGE,
                script=ScriptType.UNKNOWN,
            )
        ],
    )

    assert info.primary_language == UNKNOWN_LANGUAGE
    assert info.detection is None


def test_resolved_language_requires_detection_evidence():
    with pytest.raises(ValidationError, match="requires a detection method"):
        LanguageInfo(
            primary_language="ta",
            detection=LanguageDetection(primary_language="ta"),
            text_representations=[source_rep()],
        )


def test_a_language_with_no_detection_record_is_rejected():
    """Ingestion hardcodes data.language; copying it through is not detection."""
    with pytest.raises(ValidationError, match="requires a detection record"):
        LanguageInfo(
            primary_language="ta",
            text_representations=[source_rep()],
        )


def test_primary_language_must_agree_with_the_detection_record():
    with pytest.raises(ValidationError, match="disagrees with"):
        LanguageInfo(
            primary_language="ta",
            detection=ta_detection(primary_language="en"),
            text_representations=[source_rep()],
        )


def test_inherited_language_hint_is_kept_for_audit_alongside_detection():
    """Ingestion hardcodes data.language; the module records it without trusting it."""
    info = protest_language()

    assert info.inherited_language_hint == "ta"
    assert info.detection is not None
    assert info.detection.method is ExtractionMethod.RULE
    assert info.detection.tamil_script_ratio == 0.97


def test_title_and_summary_keep_source_wording_separate_from_translation():
    title = TitleInfo(
        text=LocalizedText(
            source=TITLE_TA,
            translated="Sanitation workers' protest at the corporation office",
            language="ta",
            translated_language="en",
        )
    )

    assert title.text.source == TITLE_TA
    assert title.is_verbatim_from_record is True


def test_a_translated_short_text_never_stands_without_its_source():
    with pytest.raises(ValidationError, match="requires the source value"):
        LocalizedText(translated="An English rendering", language="ta", translated_language="en")


def test_abstractive_summary_must_declare_confidence_and_method():
    from intelligence.models.enums import SummaryKind

    with pytest.raises(ValidationError, match="requires confidence"):
        SummaryInfo(
            text=LocalizedText(source="ஈடுபட்டனர்", language="ta"),
            kind=SummaryKind.ABSTRACTIVE,
            method=ExtractionMethod.LLM,
        )

    with pytest.raises(ValidationError, match="requires a method"):
        SummaryInfo(
            text=LocalizedText(source="ஈடுபட்டனர்", language="ta"),
            kind=SummaryKind.ABSTRACTIVE,
            confidence=0.6,
        )
