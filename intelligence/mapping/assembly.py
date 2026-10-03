"""Stage 5: one record in, one honest candidate Incident out."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Mapping, Optional

from intelligence.extraction import boilerplate, language, temporal
from intelligence.extraction.spans import (
    SourceField,
    SpanCheck,
    compute_field_hash,
    verify_evidence,
)
from intelligence.extraction.temporal import TemporalExtraction
from intelligence.mapping.record_input import (
    CONTENT_PATH,
    DISTRICT_PATH,
    EVENT_TIME_PATH,
    LANGUAGE_PATH,
    TITLE_PATH,
    MappingError,
    RecordInput,
    input_hash,
    read_record,
)
from intelligence.models.base import collect_evidence_references
from intelligence.models.enums import (
    DataOrigin,
    DistrictHintAuthority,
    ExtractionMethod,
    IncidentStatus,
    Modality,
    ResolutionState,
    SeverityLevel,
    SpanValidation,
    TimePrecision,
    TimeQualifier,
    TimeSemantics,
)
from intelligence.models.evidence import Evidence
from intelligence.models.incident import SCHEMA_VERSION, Incident
from intelligence.models.language import LocalizedText, TitleInfo
from intelligence.models.metadata import ConfidenceSummary, ProcessingMetadata
from intelligence.models.severity import Severity
from intelligence.models.spatial import SpatialHint
from intelligence.models.temporal import TimeValue

PROVIDER = "intelligence.mapping"
DEFAULT_TIMEZONE = "Asia/Kolkata"
CONFIDENCE_VERBATIM = 1.0
RULE_OVERALL = (
    "min over the components Stage 5 established; the sections it left unresolved are "
    "counted in unresolved_field_count, never scored"
)
UNRESOLVED_SECTIONS = ("relevance", "classification", "summary", "actors", "observations")
SEVERITY_PATH = "severity"
STATUS_PATH = "status"
STAGE_VERSIONS = {
    "schema": SCHEMA_VERSION,
    "evidence": "2",
    "language": "3",
    "temporal": "4",
    "mapping": "5",
}
VERIFIED = "a span no longer reproduces the field it was cut from"
CLEAN = (SpanValidation.VALIDATED, SpanValidation.NOT_APPLICABLE)


@dataclass(frozen=True)
class MappingPolicy:
    """The per-feed decisions a record cannot make for itself."""

    timezone: Optional[str] = DEFAULT_TIMEZONE
    reference: Optional[datetime] = None
    text_keys: tuple[str, ...] = (CONTENT_PATH,)
    extra_keys: tuple[str, ...] = ()
    time_trusted_record_types: frozenset[str] = frozenset()
    low_confidence_threshold: float = 0.6
    modality: Modality = Modality.TEXT
    incident_id_prefix: str = "INC"
    positional_record_ids: bool = False
    pipeline_version: str = "0.1.0"
    provider: str = PROVIDER

    def config_hash(self) -> str:
        return compute_field_hash(
            "|".join(
                (
                    SCHEMA_VERSION,
                    self.provider,
                    str(self.timezone),
                    ",".join(self.text_keys),
                    ",".join(self.extra_keys),
                    ",".join(sorted(self.time_trusted_record_types)),
                    str(self.low_confidence_threshold),
                    self.modality.value,
                    self.pipeline_version,
                )
            )
        )


@dataclass(frozen=True)
class IncidentDraft:
    """The incident, plus everything it took to make it, kept outside the contract."""

    incident: Incident
    record: RecordInput
    fields: Mapping[str, SourceField]
    split: Optional[boilerplate.BodySplit] = None
    temporal: Optional[TemporalExtraction] = None
    checks: tuple[SpanCheck, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_spans_verify(self) -> bool:
        return all(check.validation in CLEAN for check in self.checks)

    @property
    def referenced_evidence_ids(self) -> frozenset[str]:
        return collect_evidence_references(self.incident)

    @property
    def unreferenced_evidence_ids(self) -> tuple[str, ...]:
        referenced = self.referenced_evidence_ids
        return tuple(
            evidence.evidence_id
            for evidence in self.incident.evidence
            if evidence.evidence_id not in referenced
        )

    def time_roles(self) -> dict[str, Optional[str]]:
        if self.temporal is None:
            return dict.fromkeys(
                ("event_time", "publication_time", "reported_time", "retrieval_time")
            )
        return {
            "event_time": _instant(self.temporal.event_time()),
            "publication_time": _instant(self.temporal.publication_time()),
            "reported_time": _instant(self.temporal.reported_time()),
            "retrieval_time": _instant(self.temporal.retrieval),
        }

    def report(self) -> dict:
        incident = self.incident
        return {
            "incident_id": incident.incident_id,
            "record_id": self.record.record_id,
            "fields": {path: source.text_hash for path, source in self.fields.items()},
            "event_time": {
                "value": _instant(incident.event_time),
                "semantics": incident.event_time.semantics.value,
                "precision": incident.event_time.precision.value,
                "method": incident.event_time.method.value,
                "raw_text": incident.event_time.raw_text,
                "evidence_ids": list(incident.event_time.evidence_ids),
            },
            "time_roles": self.time_roles(),
            "language": {
                "primary": incident.language.primary_language,
                "script": incident.language.primary_script.value,
                "multilingual": incident.language.is_multilingual,
                "hint": incident.language.inherited_language_hint,
                "detection_confidence": incident.language.detection.confidence
                if incident.language.detection
                else None,
            },
            "district_hint": {
                "value": incident.spatial.district_hint,
                "authority": incident.spatial.district_hint_authority.value,
                "from_text": incident.spatial.district_hint_from_text,
                "state": incident.spatial.resolution_state.value,
            },
            "stamps": self.split.stamps_as_dicts() if self.split else [],
            "confidence": {
                "overall": incident.confidence.overall,
                "components": dict(incident.confidence.components),
                "unresolved_field_count": incident.confidence.unresolved_field_count,
            },
            "unreferenced_evidence": list(self.unreferenced_evidence_ids),
            "warnings": list(self.warnings),
            "review": {
                "needed": incident.review.needed,
                "reasons": [reason.value for reason in incident.review.reasons],
            },
            "inconsistencies": incident.inconsistencies(),
        }


def map_record(record: RecordInput, policy: Optional[MappingPolicy] = None) -> IncidentDraft:
    """Assemble the candidate incident this record supports, and no more than that."""
    policy = MappingPolicy() if policy is None else policy
    if not isinstance(record, RecordInput):
        record = read_record(record, text_keys=policy.text_keys, extra_keys=policy.extra_keys)

    ledger = _Ledger(record, policy)
    warnings: list[str] = list(record.notes)

    for path, text in record.texts:
        ledger.read_text(path, text)

    body_path = next((path for path in record.text_paths if path != TITLE_PATH), None)
    body_field = ledger.fields.get(body_path) if body_path else None
    split = boilerplate.split(body_field, lowercase=True) if body_field is not None else None

    derived = []
    if split is not None and body_field is not None and body_path is not None:
        reading = language.assess(body_field.text)
        identifier = language.representation_id(body_field)
        derived.append(
            split.representation(
                representation_id=f"{identifier}-normalized",
                derived_from=identifier,
                language=reading.primary_language,
                script=reading.profile.script_type,
                evidence_ids=[ledger.field_evidence(body_path).evidence_id],
            )
        )
    provenance = [ledger.field_evidence(path).evidence_id for path in record.text_paths]
    info = language.language_info(
        [ledger.fields[path] for path in record.text_paths],
        derived_representations=derived,
        inherited_hint=record.language_hint,
        source_evidence_ids=provenance,
        detection_evidence_ids=provenance,
    )
    conflict = language.hint_conflict(info)
    if conflict:
        warnings.append(conflict)

    extraction = None
    if split is not None and body_field is not None:
        extraction = temporal.extract(
            body_field,
            reference=policy.reference or record.retrieved_at,
            timezone=policy.timezone,
            split=split,
        )
        for mention in (*extraction.mentions, *extraction.publication):
            ledger.add(mention.evidence)
            for endpoint in mention.endpoints:
                ledger.add(endpoint.evidence)

    event_time = _event_time(record, policy, extraction, ledger, warnings)
    _preserved(record, ledger, warnings)
    spatial = _spatial(record, ledger)
    severity = _severity(record, ledger)

    if record.retrieved_at.tzinfo is None:
        zone = f"declared as {policy.timezone}" if policy.timezone else "undeclared"
        warnings.append(
            f"retrieved_at {record.retrieved_at.isoformat()} carries no UTC offset; the zone is "
            f"{zone} and the value was not converted"
        )
    if policy.positional_record_ids:
        warnings.append(
            f"incident_id is keyed on record_id {record.record_id!r}, which the producer derives "
            "from scrape position: it moves whenever the feed order changes"
        )
    if record.unused_data_keys:
        warnings.append("data keys this policy did not read: " + ", ".join(record.unused_data_keys))

    incident = Incident(
        incident_id=derive_incident_id(record, prefix=policy.incident_id_prefix),
        status=IncidentStatus.CANDIDATE,
        origin=DataOrigin.PIPELINE,
        language=info,
        title=_title(record, ledger),
        event_time=event_time,
        spatial=spatial,
        severity=severity,
        evidence=ledger.evidence_list(),
        supporting_record_ids=ledger.supporting_record_ids(),
        confidence=_confidence(info, event_time, severity, spatial),
        processing=ProcessingMetadata(
            pipeline_version=policy.pipeline_version,
            config_hash=policy.config_hash(),
            stage_versions=dict(STAGE_VERSIONS),
            input_record_hash=input_hash(record),
            modality=policy.modality,
            source_record_count=1,
            warnings=tuple(warnings),
        ),
    )
    incident.apply_review_flags(policy.low_confidence_threshold)

    draft = IncidentDraft(
        incident=incident,
        record=record,
        fields=ledger.fields,
        split=split,
        temporal=extraction,
        warnings=tuple(warnings),
    )
    return replace(draft, checks=verify_draft(draft))


def derive_incident_id(record: RecordInput, *, prefix: str = "INC") -> str:
    """Readable, and stable for one record: naming is not deduplication's job."""
    return f"{prefix}-{record.record_id}"


def verify_draft(draft: IncidentDraft) -> tuple[SpanCheck, ...]:
    """Replay every span against the exact field text it claims to have been cut from."""
    checks: list[SpanCheck] = []
    failures: list[str] = []
    for evidence in draft.incident.evidence:
        source = draft.fields.get(evidence.field)
        if source is None:
            failures.append(
                f"{evidence.describe()} cites field {evidence.field!r}, which this mapping never read"
            )
            continue
        check = verify_evidence(evidence, source.text)
        checks.append(check)
        if check.validation not in CLEAN:
            failures.append(check.describe())
    if failures:
        raise MappingError(f"{VERIFIED}: " + "; ".join(failures))
    return tuple(checks)


class _Ledger:
    """Every field read and every evidence produced, so nothing is quoted twice or typed by hand."""

    def __init__(self, record: RecordInput, policy: MappingPolicy):
        self.record = record
        self.policy = policy
        self.fields: dict[str, SourceField] = {}
        self.evidences: dict[str, Evidence] = {}
        self._field_evidence: dict[str, Evidence] = {}

    def _source(self, path: str, text: str) -> SourceField:
        existing = self.fields.get(path)
        if existing is not None:
            if existing.text != text:
                raise MappingError(
                    f"field {path!r} was read twice with different text: {existing.text!r} "
                    f"and {text!r}; one record holds one version of a field"
                )
            return existing
        source = SourceField(
            record_id=self.record.record_id,
            source_id=self.record.source_id,
            source_type=self.record.source_type,
            field=path,
            text=text,
            source_url=self.record.source_url,
            raw_reference=self.record.raw_reference,
            retrieved_at=self.record.retrieved_at,
        )
        self.fields[path] = source
        return source

    def read_text(self, path: str, text: str) -> SourceField:
        return self._source(path, text)

    def field_evidence(self, path: str) -> Evidence:
        cached = self._field_evidence.get(path)
        if cached is not None:
            return cached
        evidence = self.add(
            self.fields[path].metadata_evidence(
                confidence=CONFIDENCE_VERBATIM,
                notes=f"{path} copied verbatim from the record, held untouched",
            )
        )
        self._field_evidence[path] = evidence
        return evidence

    def quote(self, path: str, value: str, *, notes: str) -> Evidence:
        source = self._source(path, value)
        return self.add(
            source.evidence(
                value,
                method=ExtractionMethod.SOURCE_METADATA,
                confidence=CONFIDENCE_VERBATIM,
                notes=notes,
            )
        )

    def add(self, evidence: Evidence) -> Evidence:
        self.evidences.setdefault(evidence.evidence_id, evidence)
        return evidence

    def evidence_list(self) -> list[Evidence]:
        return list(self.evidences.values())

    def supporting_record_ids(self) -> list[str]:
        return [self.record.record_id] if self.evidences else []


def _title(record: RecordInput, ledger: _Ledger) -> TitleInfo:
    text = record.title
    if text is None or TITLE_PATH not in ledger.fields:
        return TitleInfo()
    evidence = ledger.field_evidence(TITLE_PATH)
    return TitleInfo(
        text=LocalizedText(
            source=text,
            language=language.assess(text).primary_language,
            evidence_ids=[evidence.evidence_id],
        ),
        confidence=CONFIDENCE_VERBATIM,
        evidence_ids=[evidence.evidence_id],
    )


def _event_time(
    record: RecordInput,
    policy: MappingPolicy,
    extraction: Optional[TemporalExtraction],
    ledger: _Ledger,
    warnings: list[str],
) -> TimeValue:
    if record.record_type in policy.time_trusted_record_types and record.event_time is not None:
        rendered = record.event_time.isoformat()
        evidence = ledger.quote(
            EVENT_TIME_PATH,
            rendered,
            notes=(
                f"the record's own event_time, trusted because record_type "
                f"{record.record_type!r} states a valid time rather than copying a page stamp"
            ),
        )
        return TimeValue(
            value=record.event_time,
            precision=time_precision_of(record.event_time),
            qualifier=TimeQualifier.EXACT,
            semantics=TimeSemantics.EVENT_TIME,
            timezone=policy.timezone,
            method=ExtractionMethod.SOURCE_METADATA,
            confidence=CONFIDENCE_VERBATIM,
            evidence_ids=[evidence.evidence_id],
            notes="valid time as stated by the source record",
        )

    if record.event_time is not None:
        warnings.append(
            f"record event_time {record.event_time.isoformat()} was not used: for record_type "
            f"{record.record_type!r} it is the publish stamp captured from the page, and event "
            "time is read from the article text instead"
        )

    if extraction is None:
        return temporal.unresolved_time(
            timezone=policy.timezone,
            notes="the record carries no body text to read a time from",
        )

    event = extraction.event_time()
    if event.value is not None:
        return event

    publication = extraction.publication_time()
    if publication.value is not None:
        warnings.append(
            "the article text states no event time, so the publish stamp read from the content "
            f"stands in as {publication.value.isoformat()} with semantics "
            f"{publication.semantics.value}"
        )
        return publication
    return event


def _spatial(record: RecordInput, ledger: _Ledger) -> SpatialHint:
    if record.district is None:
        return SpatialHint(
            resolution_state=ResolutionState.NOT_ATTEMPTED,
            notes="no district asserted by the source, and no place text is read before Stage 6",
        )
    evidence = ledger.quote(
        DISTRICT_PATH,
        record.district,
        notes=(
            "the district the feed configuration asserted, which is not a statement about where "
            "this event happened"
        ),
    )
    return SpatialHint(
        district_hint=record.district,
        district_hint_authority=DistrictHintAuthority.SOURCE_CONFIGURATION,
        district_hint_confidence=None,
        district_hint_from_text=None,
        resolution_state=ResolutionState.PENDING_GIS,
        notes=(
            f"the district comes from feed configuration, cited as {evidence.describe()}; "
            "Stage 5 reads no place mentions and resolves no geography"
        ),
    )


def _severity(record: RecordInput, ledger: _Ledger) -> Severity:
    if record.severity is None:
        return Severity.unresolved(
            "no severity cue was read: Stage 5 extracts no severity"
        )
    evidence = ledger.quote(
        SEVERITY_PATH,
        record.severity,
        notes=(
            "the severity string the source recorded, held without being translated into the "
            "Intelligence severity scale"
        ),
    )
    return Severity(
        level=SeverityLevel.UNRESOLVED,
        notes=(
            f"the source recorded severity {record.severity!r} in its own vocabulary, cited as "
            f"{evidence.describe()} and deliberately not mapped to a level"
        ),
        evidence_ids=[evidence.evidence_id],
    )


def _preserved(record: RecordInput, ledger: _Ledger, warnings: list[str]) -> None:
    """Values Stage 5 must keep but has no contract slot for: cited, then said out loud."""
    if record.status is not None:
        evidence = ledger.quote(
            STATUS_PATH,
            record.status,
            notes=(
                "the status string the source recorded; Incident.status is the Intelligence "
                "workflow state and is not a translation of it"
            ),
        )
        warnings.append(
            f"record status {record.status!r} is held as provenance only, cited as "
            f"{evidence.describe()}; the incident stays a candidate"
        )
    if record.language_hint is not None:
        evidence = ledger.quote(
            LANGUAGE_PATH,
            record.language_hint,
            notes="the language the feed labelled this record with, kept as a hint",
        )
        warnings.append(
            f"data.language {record.language_hint!r} is an inherited hint only, cited as "
            f"{evidence.describe()}; the language was decided from script evidence"
        )
    for path, rendered in record.scalars:
        ledger.quote(
            path,
            rendered,
            notes=f"{path} as the source recorded it, rendered to text and quoted back",
        )
    if record.scalars:
        warnings.append(
            "non-text values were cited as renderings rather than as body text: "
            + ", ".join(path for path, _ in record.scalars)
        )


def _confidence(
    info, event_time: TimeValue, severity: Severity, spatial: SpatialHint
) -> ConfidenceSummary:
    count = _unresolved_field_count(severity, spatial)
    components: dict[str, float] = {}
    if info.detection is not None and info.detection.confidence is not None:
        components["language"] = info.detection.confidence
    if event_time.confidence is not None:
        components["event_time"] = event_time.confidence
    if not components:
        return ConfidenceSummary(unresolved_field_count=count)
    return ConfidenceSummary(
        overall=min(components.values()),
        components=components,
        rule=RULE_OVERALL,
        unresolved_field_count=count,
    )


def _unresolved_field_count(severity: Severity, spatial: SpatialHint) -> int:
    gaps = list(UNRESOLVED_SECTIONS)
    if severity.level is SeverityLevel.UNRESOLVED:
        gaps.append("severity")
    if not spatial.mentions:
        gaps.append("spatial.mentions")
    if spatial.resolution_state is not ResolutionState.RESOLVED:
        gaps.append("spatial.resolution")
    return len(gaps)


def time_precision_of(value: datetime) -> TimePrecision:
    """What the stored instant can honestly claim: a date, a minute, or a second."""
    if value.microsecond or value.second:
        return TimePrecision.SECOND
    if not (value.hour or value.minute):
        return TimePrecision.DAY
    return TimePrecision.MINUTE


def _instant(value: Optional[TimeValue]) -> Optional[str]:
    if value is None or value.value is None:
        return None
    return value.value.isoformat()
