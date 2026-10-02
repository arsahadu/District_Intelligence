"""Reusable contract builders for Intelligence tests."""

from __future__ import annotations

import hashlib
from datetime import datetime

from intelligence.models.actors import Actor
from intelligence.models.classification import ClassificationInfo, DepartmentHint, RelevanceInfo
from intelligence.models.enums import ActorRole, ActorType, Department, DepartmentHintBasis
from intelligence.models.enums import DistrictHintAuthority, EventType, ExtractionMethod, GranularityLevel
from intelligence.models.enums import MentionRole, MentionType, ObservationKind, ObservationQualifier
from intelligence.models.enums import RelevanceState, ResolutionState, ScriptType, SpanValidation
from intelligence.models.enums import SummaryKind, TextRole, TimePrecision, TimeQualifier, TimeSemantics
from intelligence.models.evidence import Evidence
from intelligence.models.incident import Incident
from intelligence.models.language import (
    LanguageDetection,
    LanguageInfo,
    LocalizedText,
    SummaryInfo,
    TextRepresentation,
    TitleInfo,
)
from intelligence.models.metadata import ConfidenceSummary, DedupMetadata, ProcessingMetadata
from intelligence.models.quantities import Observation
from intelligence.models.severity import Severity
from intelligence.models.spatial import LocationMention, SpatialHint
from intelligence.models.temporal import TimeValue

RECORD_ID = "NEWS-MDU-TEST-0001"
SOURCE_ID = "dinamalar"
SOURCE_TYPE = "news"
SOURCE_URL = "https://www.dinamalar.com/district/291"

TITLE_TA = "துாய்மைப் பணியாளர்கள் மாநகராட்சி அலுவலகத்தில் மனு அளித்த ஆர்ப்பாட்டம்"

#: Mirrors the real corpus: publish boilerplate, then Tamil body text.
CONTENT_TA = (
    "UPDATED : அக் 02, 2026 12:00 AM ADDED : அக் 01, 2026 09:46 PM "
    "அவர் லேண்ட் நிறுவன ஒப்பந்தத்தை ரத்து செய்யக்கோரி துாய்மைப் பணியாளர்கள் "
    "மதுரையில் உள்ள மாநகராட்சி அலுவலகத்தில் 4800 மனுக்களை கமிஷனரிடம் அளித்தனர். "
    "தொழிலாளர் நல வாரிய ஊழியர்கள் ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்."
)

#: A Kumbakonam incident reported through the Madurai bench of the High Court.
BENCH_CONTENT_TA = (
    "கும்பகோணத்தில் அரசின் மதுபான விடுதியை காலி செய்ய மறுத்ததால் விடுதி "
    "உரிமையாளர் மீது வழக்கு தொடரப்பட்டது; உயர்நீதிமன்ற மதுரைக் கிளை "
    "விசாரணைக்கு இடைக்கால தடை விதித்தது."
)


def field_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span_evidence(
    evidence_id: str,
    *,
    field: str = "data.content",
    source_text: str = CONTENT_TA,
    quote: str,
    method: ExtractionMethod = ExtractionMethod.RULE,
    confidence: float = 0.95,
    record_id: str = RECORD_ID,
    **overrides,
) -> Evidence:
    """Build evidence with offsets computed from the real text."""
    start = source_text.index(quote)
    payload = {
        "evidence_id": evidence_id,
        "record_id": record_id,
        "source_id": SOURCE_ID,
        "source_type": SOURCE_TYPE,
        "field": field,
        "method": method,
        "quote": quote,
        "char_start": start,
        "char_end": start + len(quote),
        "field_text_hash": field_hash(source_text),
        "span_validation": SpanValidation.VALIDATED,
        "source_url": SOURCE_URL,
        "raw_reference": SOURCE_URL,
        "confidence": confidence,
    }
    payload.update(overrides)
    return Evidence(**payload)


def metadata_evidence(evidence_id: str, *, field: str = "retrieved_at", quote: str) -> Evidence:
    """Evidence copied straight out of the record: no offsets required."""
    return Evidence(
        evidence_id=evidence_id,
        record_id=RECORD_ID,
        source_id=SOURCE_ID,
        source_type=SOURCE_TYPE,
        field=field,
        method=ExtractionMethod.SOURCE_METADATA,
        quote=quote,
        span_validation="not_applicable",
    )


def protest_evidence() -> list[Evidence]:
    return [
        span_evidence(
            "ev-protest",
            quote="ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்",
            method=ExtractionMethod.DICTIONARY,
        ),
        span_evidence(
            "ev-petitions",
            quote="4800 மனுக்களை",
            method=ExtractionMethod.REGEX,
        ),
        span_evidence(
            "ev-corporation",
            quote="மாநகராட்சி அலுவலகத்தில்",
            method=ExtractionMethod.DICTIONARY,
        ),
        span_evidence(
            "ev-commissioner",
            quote="கமிஷனரிடம்",
            method=ExtractionMethod.DICTIONARY,
        ),
        span_evidence(
            "ev-time",
            quote="அக் 01, 2026 09:46 PM",
            method=ExtractionMethod.REGEX,
        ),
        span_evidence(
            "ev-madurai",
            quote="மதுரையில்",
            method=ExtractionMethod.DICTIONARY,
        ),
    ]


def protest_language() -> LanguageInfo:
    return LanguageInfo(
        primary_language="ta",
        primary_script=ScriptType.TAMIL,
        inherited_language_hint="ta",
        detection=LanguageDetection(
            primary_language="ta",
            script_ratios={"tamil": 0.97, "latin": 0.03},
            tamil_script_ratio=0.97,
            latin_script_ratio=0.03,
            method=ExtractionMethod.RULE,
            confidence=0.98,
        ),
        text_representations=[
            TextRepresentation(
                representation_id="rep-source",
                text=CONTENT_TA,
                role=TextRole.SOURCE,
                language="ta",
                script=ScriptType.TAMIL,
                method=ExtractionMethod.SOURCE_METADATA,
            ),
            TextRepresentation(
                representation_id="rep-normalized",
                text=CONTENT_TA.replace("UPDATED : அக் 02, 2026 12:00 AM ADDED : அக் 01, 2026 09:46 PM ", ""),
                role=TextRole.NORMALIZED,
                language="ta",
                script=ScriptType.TAMIL,
                derived_from="rep-source",
                provider="intelligence.normalize.boilerplate",
                method=ExtractionMethod.RULE,
                confidence=1.0,
            ),
        ],
    )


def protest_incident(*, with_review: bool = False) -> Incident:
    """A fully populated, internally consistent Tamil incident."""
    evidence = protest_evidence()

    incident = Incident(
        incident_id="INC-TEST-0001",
        evidence=evidence,
        supporting_record_ids=[RECORD_ID],
        language=protest_language(),
        title=TitleInfo(
            text=LocalizedText(source=TITLE_TA, language="ta"),
            confidence=1.0,
        ),
        relevance=RelevanceInfo(
            state=RelevanceState.INCIDENT,
            is_incident=True,
            reason="protest action against a contractor agreement",
            method=ExtractionMethod.RULE,
            confidence=0.9,
            evidence_ids=["ev-protest"],
        ),
        classification=ClassificationInfo(
            event_type=EventType.PROTEST_OR_STRIKE,
            category_scores={
                "protest_or_strike": 0.82,
                "public_disturbance": 0.21,
                "service_delivery_delay": 0.05,
            },
            family="law_and_order",
            taxonomy_version="2026.10-stage1",
            departments=[
                DepartmentHint(
                    department=Department.POLICE,
                    basis=DepartmentHintBasis.TAXONOMY,
                    confidence=0.7,
                ),
                DepartmentHint(
                    department=Department.URBAN_LOCAL_BODIES,
                    basis=DepartmentHintBasis.TEXT_MENTION,
                    confidence=0.75,
                    evidence_ids=["ev-corporation"],
                ),
            ],
            method=ExtractionMethod.RULE,
            confidence=0.82,
            evidence_ids=["ev-protest"],
        ),
        actors=[
            Actor(
                actor_id="act-commissioner",
                name_text="கமிஷனர்",
                name_normalized="கமிஷனர்",
                transliterated_latin="commissioner",
                official_title="கமிஷனர்",
                actor_type=ActorType.GOVERNMENT_OFFICIAL,
                role=ActorRole.RESPONDING_AUTHORITY,
                script=ScriptType.TAMIL,
                method=ExtractionMethod.DICTIONARY,
                confidence=0.75,
                evidence_ids=["ev-commissioner"],
            )
        ],
        observations=[
            Observation(
                observation_id="obs-petitions",
                kind=ObservationKind.PETITION_COUNT,
                value_numeric=4800,
                raw_text="4800 மனுக்களை",
                qualifier=ObservationQualifier.EXACT,
                method=ExtractionMethod.REGEX,
                confidence=0.9,
                evidence_ids=["ev-petitions"],
            )
        ],
        event_time=TimeValue(
            value=datetime(2026, 10, 1, 21, 46),
            precision=TimePrecision.MINUTE,
            qualifier=TimeQualifier.EXACT,
            semantics=TimeSemantics.PUBLICATION_TIME,
            raw_text="அக் 01, 2026 09:46 PM",
            timezone="Asia/Kolkata",
            method=ExtractionMethod.REGEX,
            confidence=0.95,
            evidence_ids=["ev-time"],
        ),
        reported_at=datetime(2026, 10, 2, 6, 0),
        spatial=SpatialHint(
            mentions=[
                LocationMention(
                    mention_id="men-office",
                    text="மாநகராட்சி அலுவலகத்தில்",
                    text_normalized="மாநகராட்சி அலுவலகம்",
                    transliterated_latin="managaram",
                    script=ScriptType.TAMIL,
                    mention_type=MentionType.INSTITUTION,
                    granularity=GranularityLevel.LANDMARK,
                    role=MentionRole.EVENT_LOCATION,
                    context_window="மதுரையில் உள்ள மாநகராட்சி அலுவலகத்தில் 4800",
                    is_event_location_candidate=True,
                    method=ExtractionMethod.DICTIONARY,
                    confidence=0.8,
                    evidence_ids=["ev-corporation"],
                ),
                LocationMention(
                    mention_id="men-madurai",
                    text="மதுரையில்",
                    text_normalized="மதுரை",
                    transliterated_latin="madurai",
                    script=ScriptType.TAMIL,
                    mention_type=MentionType.DISTRICT,
                    granularity=GranularityLevel.DISTRICT,
                    role=MentionRole.EVENT_CONTAINER,
                    is_event_location_candidate=True,
                    gazetteer_matched=True,
                    gazetteer_source="tn_places",
                    method=ExtractionMethod.DICTIONARY,
                    confidence=0.85,
                    evidence_ids=["ev-madurai"],
                ),
            ],
            best_event_location_mention_id="men-madurai",
            district_hint="Madurai",
            district_hint_authority=DistrictHintAuthority.SOURCE_CONFIGURATION,
            district_hint_confidence=0.4,
            resolution_state=ResolutionState.PENDING_GIS,
        ),
        severity=Severity.unresolved("no casualty, damage or disruption cue present"),
        confidence=ConfidenceSummary(
            overall=0.82,
            components={
                "event_type": 0.82,
                "event_time": 0.95,
                "spatial": 0.85,
                "observations": 0.9,
            },
            rule="min(event_type, spatial)",
        ),
        dedup=DedupMetadata(algorithm_version="fp-1"),
        processing=ProcessingMetadata(
            pipeline_version="0.1.0",
            source_record_count=1,
            modality="text",
        ),
    )
    if with_review:
        incident.apply_review_flags()
    return incident


def unresolved_incident() -> Incident:
    """Every field at its default: the legal 'we know nothing yet' state."""
    return Incident(incident_id="INC-EMPTY-0001")


def bench_incident() -> Incident:
    """Kumbakonam incident reported via the Madurai bench."""
    evidence = [
        span_evidence("ev-bench-case", quote="வழக்கு தொடரப்பட்டது", source_text=BENCH_CONTENT_TA),
        span_evidence(
            "ev-bench-ban",
            quote="இடைக்கால தடை விதித்தது",
            source_text=BENCH_CONTENT_TA,
        ),
        span_evidence(
            "ev-bench-kumbakonam",
            quote="கும்பகோணத்தில்",
            source_text=BENCH_CONTENT_TA,
        ),
        span_evidence("ev-bench-madurai", quote="மதுரைக்", source_text=BENCH_CONTENT_TA),
    ]
    return Incident(
        incident_id="INC-TEST-BENCH-0001",
        evidence=evidence,
        supporting_record_ids=[RECORD_ID],
        language=LanguageInfo(
            primary_language="ta",
            primary_script=ScriptType.TAMIL,
            detection=LanguageDetection(
                primary_language="ta",
                tamil_script_ratio=0.94,
                latin_script_ratio=0.06,
                method=ExtractionMethod.RULE,
                confidence=0.96,
            ),
            text_representations=[
                TextRepresentation(
                    representation_id="rep-source",
                    text=BENCH_CONTENT_TA,
                    role=TextRole.SOURCE,
                    language="ta",
                    script=ScriptType.TAMIL,
                    method=ExtractionMethod.SOURCE_METADATA,
                )
            ],
        ),
        relevance=RelevanceInfo(
            state=RelevanceState.INCIDENT,
            is_incident=True,
            method=ExtractionMethod.RULE,
            confidence=0.8,
            evidence_ids=["ev-bench-case"],
        ),
        classification=ClassificationInfo(
            event_type=EventType.LEGAL_PROCEEDING_OR_ORDER,
            method=ExtractionMethod.RULE,
            confidence=0.78,
            evidence_ids=["ev-bench-ban"],
        ),
        spatial=SpatialHint(
            mentions=[
                LocationMention(
                    mention_id="men-kumbakonam",
                    text="கும்பகோணத்தில்",
                    text_normalized="கும்பகோணம்",
                    transliterated_latin="kumbakonam",
                    mention_type=MentionType.TOWN,
                    granularity=GranularityLevel.TOWN,
                    role=MentionRole.EVENT_LOCATION,
                    is_event_location_candidate=True,
                    method=ExtractionMethod.DICTIONARY,
                    confidence=0.7,
                    evidence_ids=["ev-bench-kumbakonam"],
                ),
                LocationMention(
                    mention_id="men-madurai-bench",
                    text="மதுரைக்",
                    text_normalized="மதுரை",
                    transliterated_latin="madurai",
                    mention_type=MentionType.INSTITUTION,
                    granularity=GranularityLevel.DISTRICT,
                    role=MentionRole.INSTITUTION_NAME,
                    context_window="உயர்நீதிமன்ற மதுரைக் கிளை",
                    is_event_location_candidate=False,
                    method=ExtractionMethod.DICTIONARY,
                    confidence=0.9,
                    evidence_ids=["ev-bench-madurai"],
                ),
            ],
            district_hint="Madurai",
            district_hint_authority=DistrictHintAuthority.SOURCE_CONFIGURATION,
            competing_districts=["Thanjavur"],
            resolution_state=ResolutionState.AMBIGUOUS,
            notes="feed district says Madurai; the event text says Kumbakonam",
        ),
        summary=SummaryInfo(kind=SummaryKind.UNRESOLVED),
    )
