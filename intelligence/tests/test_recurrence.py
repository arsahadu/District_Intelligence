from __future__ import annotations

import io
import json
import re
from datetime import date
from typing import Any, Callable, Optional

import pytest
from pydantic import ValidationError

from intelligence.checkpoint import CheckpointError
from intelligence.contract import (
    Department,
    EventType,
    Incident,
    IncidentCategory,
    ReviewState,
    TimePrecision,
)
from intelligence.fallback import Chain
from intelligence.intelligence import extract_incident
from intelligence.llm import LLMError, LLMRequest, QuotaExhaustedError
from intelligence.linking import link_incidents
from intelligence.models.enums import ExtractionMethod, SeverityLevel, SpanValidation
from intelligence.pipeline import EXIT_OK, main
from intelligence.recurrence import (
    ISSUES_FILENAME,
    ISSUE_PROMPT_DIGEST,
    ISSUE_PROMPT_VERSION,
    ISSUE_RULES,
    ISSUE_SCHEMA,
    MAX_EVENTS_PER_BUCKET,
    RECURRING_FILENAME,
    Filters,
    IssueFamily,
    IssueReport,
    IssueStore,
    candidate_buckets,
    event_units,
    family_id_for,
    group_request,
    incident_when,
    print_issues,
    recurring_issues,
)
from intelligence.tests.builders import listing, quote, responder, water_answer
from intelligence.tests.record_fixtures import base_record, waterlogging
from intelligence.tests.test_linking import (
    ACCIDENT_TA,
    NOW,
    Answering,
    flood_en,
    flood_ta,
    judged,
    never,
    report,
    verdict,
)

SUPPLY_TA = (
    "மதுரை: கருங்காலக்குடி கிராமத்தில் செப்டம்பர் 20 அன்று குடிநீர் தொட்டி நிரம்பவில்லை. "
    "நான்கு வாரமாக பெண்கள் காலைதோறும் லேரி வண்டிக்காக காத்திருக்கின்றனர். நகராட்சி "
    "குடிநீர் விநியோகப் பிரிவு இதுவரை பதில் அளிக்கவில்லை."
)
WELL_TA = (
    "மதுரை: கருங்காலக்குடி கிராமத்தின் தெற்குப் பகுதியில் அக்டோபர் 2 அன்று ஆழ் கிணறு "
    "முற்றிலும் காய்ந்தது. குடிநீர் தட்டுப்பாட்டால் பள்ளி குழந்தைகள் வீடு திரும்பினர். ஊராட்சி "
    "மன்ற கணக்காளர் புதிய கிணறு தேவை என்று தெரிவித்தார்."
)
CANAL_TA = (
    "மதுரை: கருங்காலக்குடி அருகே செப்டம்பர் 28 அன்று கடையன்கண்ணான் கால்வாய் உடைந்து "
    "நெல் வயல்களில் தண்ணீர் புகுந்தது. விவசாயிகள் நட்டம் கேட்டு மறியலில் ஈடுபட்டனர். "
    "பாசனத்துறை அலுவலர் உடனே பார்க்க வேண்டும் என்று கோரினார்."
)
CLEANED = "நகராட்சி பணியாளர்கள் நீக்களை அகற்றினர்"

CONTENTS: dict[str, str] = {}


def note(
    record_id: str,
    content: str,
    *,
    kind: str,
    kind_quote: str,
    category: Optional[str] = None,
    category_quote: Optional[str] = None,
    department: Optional[str] = None,
    department_quote: Optional[str] = None,
    severity: Optional[str] = None,
    severity_quote: Optional[str] = None,
    status: str = "reported",
    status_quote: Optional[str] = None,
    place: Optional[str] = None,
    place_quote: Optional[str] = None,
    location_type: str = "village",
    location_fields: Optional[dict[str, Any]] = None,
    when: Optional[str] = None,
    date_quote: Optional[str] = None,
    language: str = "ta",
    source_id: str = "dinamalar",
    published: Optional[str] = "2026-10-06T09:00:00",
    **record_fields: Any,
) -> Incident:
    record = base_record(
        record_id=record_id,
        source_id=source_id,
        title=f"report {record_id}",
        event_time=published,
        data={"content": content, "language": language},
        source_url=f"https://news.example.test/{record_id}",
        raw_reference=f"https://news.example.test/{record_id}",
        **record_fields,
    )
    answer: dict[str, Any] = {
        "record_kind": "incident",
        "description": quote(content, content),
        "incident_type": quote(content, kind_quote) | {"value": kind},
        "event_status": quote(content, status_quote or kind_quote) | {"value": status},
    }
    for name, value, words in (
        ("category", category, category_quote),
        ("department", department, department_quote),
        ("severity", severity, severity_quote),
    ):
        if value is not None:
            answer[name] = quote(content, words or kind_quote) | {"value": value}
    if place is not None:
        answer["locations"] = [
            quote(content, place_quote or content)
            | {
                "text": place,
                "normalized_name": place,
                "location_type": location_type,
                "role": "event_location",
            }
            | (location_fields or {})
        ]
    if when is not None:
        answer["event_time"] = quote(content, date_quote or content) | {
            "value": when,
            "precision": "day",
        }
    CONTENTS[record_id] = content
    return extract_incident(record, provider=Answering(lambda request: answer))


def flood(
    record_id: str,
    spoken: str,
    day: str,
    location_fields: Optional[dict[str, Any]] = None,
    **fields: Any,
) -> Incident:
    content = (
        f"சோழவந்தான் கிராமத்தில் {spoken} வைகை ஆற்று வெள்ளம் 40 வீடுகளை மூழ்கடித்தது. {CLEANED}"
    )
    return note(
        record_id,
        content,
        kind="flood",
        kind_quote="வைகை ஆற்று வெள்ளம்",
        place="Sholavandan",
        place_quote="சோழவந்தான் கிராமத்தில்",
        location_fields=location_fields or {"village": "Sholavandan"},
        when=f"{day}T00:00:00",
        date_quote=spoken,
        **fields,
    )


def supply(**fields: Any) -> Incident:
    fields.setdefault("category", "water")
    fields.setdefault("category_quote", "குடிநீர் தொட்டி நிரம்பவில்லை")
    fields.setdefault("department", "water_resources")
    fields.setdefault("department_quote", "நகராட்சி குடிநீர் விநியோகப் பிரிவு")
    fields.setdefault("severity", "moderate")
    fields.setdefault("severity_quote", "நான்கு வாரமாக")
    fields.setdefault("status_quote", "காத்திருக்கின்றனர்")
    fields.setdefault("place", "Karungalakudi")
    fields.setdefault("place_quote", "கருங்காலக்குடி கிராமத்தில்")
    fields.setdefault("location_fields", {"village": "Karungalakudi"})
    fields.setdefault("when", "2026-09-20T00:00:00")
    fields.setdefault("date_quote", "செப்டம்பர் 20 அன்று")
    return note(
        fields.pop("record_id", "NEWS-SUPPLY"),
        SUPPLY_TA,
        kind="drinking_water_shortage",
        kind_quote="குடிநீர் தொட்டி நிரம்பவில்லை",
        **fields,
    )


def well(**fields: Any) -> Incident:
    fields.setdefault("category", "water")
    fields.setdefault("category_quote", "குடிநீர் தட்டுப்பாட்டால்")
    fields.setdefault("department", "rural_development_panchayat_raj")
    fields.setdefault("department_quote", "ஊராட்சி மன்ற கணக்காளர்")
    fields.setdefault("severity", "high")
    fields.setdefault("severity_quote", "பள்ளி குழந்தைகள்")
    fields.setdefault("place", "Karungalakudi")
    fields.setdefault("place_quote", "கருங்காலக்குடி கிராமத்தின்")
    fields.setdefault("location_fields", {"village": "Karungalakudi"})
    fields.setdefault("when", "2026-10-02T00:00:00")
    fields.setdefault("date_quote", "அக்டோபர் 2 அன்று")
    return note(
        fields.pop("record_id", "NEWS-WELL"),
        WELL_TA,
        kind="borewell_or_well_issue",
        kind_quote="ஆழ் கிணறு முற்றிலும் காய்ந்தது",
        **fields,
    )


def canal(**fields: Any) -> Incident:
    fields.setdefault("category", "disaster")
    fields.setdefault("category_quote", "நெல் வயல்களில் தண்ணீர் புகுந்தது")
    fields.setdefault("department", "water_resources")
    fields.setdefault("department_quote", "பாசனத்துறை அலுவலர்")
    fields.setdefault("place", "Karungalakudi")
    fields.setdefault("place_quote", "கருங்காலக்குடி அருகே")
    fields.setdefault("location_fields", {"village": "Karungalakudi"})
    fields.setdefault("when", "2026-09-28T00:00:00")
    fields.setdefault("date_quote", "செப்டம்பர் 28 அன்று")
    return note(
        fields.pop("record_id", "NEWS-CANAL"),
        CANAL_TA,
        kind="canal_or_water_body_issue",
        kind_quote="கால்வாய் உடைந்து",
        **fields,
    )


def undated(incident: Incident) -> Incident:
    stripped = incident.model_copy(deep=True)
    stripped.event_time = None
    stripped.event_time_precision = TimePrecision.UNKNOWN
    stripped.provenance.declared_event_time = None
    stripped.provenance.retrieved_at = None
    return stripped


def per_event(request: LLMRequest) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in request.user.splitlines():
        if re.match(r"^Event \d+ \(", line):
            rows.append([])
            continue
        cited = re.match(r"^\[([^\]]+)\]", line)
        if cited and rows:
            rows[-1].append(cited.group(1))
    return rows


def one_from_each(request: LLMRequest) -> list[str]:
    return [rows[0] for rows in per_event(request) if rows]


def grouping(**changes: Any) -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        family = {
            "label": "One problem keeps returning",
            "description": "The same problem keeps happening where these events happened.",
            "events": list(range(1, len(per_event(request)) + 1)),
            "evidence_ids": one_from_each(request),
            "confidence": 0.9,
            "department": "water_resources",
            "action": "Test the supply and repair the line before the next failure.",
            "why_separate": [],
        } | changes
        return {
            "families": [family],
            "alone": [],
            "explanation": "Every event shown was grouped into one issue.",
        }

    return answer


def leaves_alone(request: LLMRequest) -> dict[str, Any]:
    return {
        "families": [],
        "alone": list(range(1, len(per_event(request)) + 1)),
        "explanation": "These events describe different problems.",
    }


def a_flood() -> Incident:
    return flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02")


def b_flood() -> Incident:
    return flood("NEWS-B", "செப்டம்பர் 14 அன்று", "2026-09-14")


def grouped(
    incidents: list[Incident],
    answer: Optional[Callable] = None,
    **fields: Any,
) -> tuple[Answering, IssueReport]:
    judge = Answering(answer or grouping())
    found = recurring_issues(
        incidents, chain_factory=lambda: Chain.single(judge), now=NOW, **fields
    )
    return judge, found


def test_the_grouping_prompt_is_pinned_and_keeps_a_suggestion_out_of_the_records():
    assert ISSUE_PROMPT_VERSION == "issue-1"
    assert ISSUE_PROMPT_DIGEST == "6a0e74acd2859e76"
    for phrase in (
        "Group by what the problem is, not by the words a report used",
        "Sharing a place, a district, a category or a department never makes one family",
        "A family needs at least two events",
        "never state or imply that an inspection, repair, payment, hearing or closure "
        "already happened",
        "Cite in evidence_ids only ids printed in square brackets",
        "when you are guessing, answer below 0.5",
    ):
        assert phrase in ISSUE_RULES


def test_the_model_is_asked_for_a_grouping_and_never_for_a_count():
    units = event_units([a_flood(), b_flood()], [])
    buckets = candidate_buckets(units)
    request = group_request(buckets[0])
    schema = json.dumps(ISSUE_SCHEMA)

    assert len(buckets) == 1 and request.json_schema == ISSUE_SCHEMA
    assert "incidents" not in schema and "records" not in schema and "per_month" not in schema
    assert "date: 2026-09-14 (from event_time)" in request.user
    assert "incidents: INC-NEWS-B | records: NEWS-B" in request.user
    assert "places: Sholavandan (village)" in request.user
    assert "recorded status: INC-NEWS-B: reported" in request.user
    assert "shared signals: incident_type flood" in request.user
    assert "are already distinct happenings" in request.user
    assert request.meta == {"bucket_id": buckets[0].bucket_id, "prompt_version": "issue-1"}


def test_two_articles_about_one_flood_count_once_when_the_linker_merged_them():
    tamil, english = flood_ta(), flood_en()
    _, chain = judged(verdict())
    decided = link_incidents([tamil, english], chain_factory=chain, now=NOW)

    judge, found = grouped([tamil, english, flood("NEWS-D", "நவம்பர் 20 அன்று", "2026-11-20")],
                            links=decided.links)

    assert (found.deduplicated, found.links, found.units, found.buckets, found.asked) == (
        True, 1, 2, 1, 1)
    assert len(judge.asked) == 1
    family = found.families[0]
    assert (family.events, family.incidents, family.records) == (2, 3, 3)
    assert family.review is ReviewState.ACCEPTED and family.findings == []
    merged = [ref for ref in family.references if ref.record_id in {"NEWS-A", "NEWS-B"}]
    assert len(merged) == 2 and merged[0].event_id == merged[1].event_id
    assert sorted(ref.record_id for ref in family.references) == ["NEWS-A", "NEWS-B", "NEWS-D"]


def test_without_a_link_decision_each_article_about_one_happening_counts_again():
    _, found = grouped([flood_ta(), flood_en(),
                        flood("NEWS-D", "நவம்பர் 20 அன்று", "2026-11-20")])

    assert (found.deduplicated, found.units) == (False, 3)
    assert found.families[0].events == 3 and found.families[0].incidents == 3
    assert any(
        "each accepted incident was counted as its own event" in line
        for line in found.limitations()
    )


def test_two_floods_in_one_village_become_one_issue_the_pipeline_counted():
    _, found = grouped([a_flood(), b_flood()])

    family = found.families[0]
    assert (family.events, family.incidents, family.records) == (2, 2, 2)
    assert family.label == "One problem keeps returning"
    assert (family.first_event, family.last_event, family.span_days) == (
        date(2026, 8, 2), date(2026, 9, 14), 43)
    assert family.per_month == {"2026-08": 1, "2026-09": 1}
    assert family.geographic_precision == "village"
    assert [(area.level, area.name) for area in family.areas] == [("village", "Sholavandan")]
    assert family.source_urls == [
        "https://news.example.test/NEWS-A", "https://news.example.test/NEWS-B"]
    assert family.incident_types == ["flood"] and family.categories == []
    assert (family.provider, family.model, family.method, family.decided_at) == (
        "scripted", "gpt-oss-120b", ExtractionMethod.LLM, NOW)
    assert family.change is None and "shorter than the 61 days" in family.comparison


def test_every_count_area_and_reference_a_family_carries_is_traced_to_a_record():
    incidents = [a_flood(), b_flood()]
    by_record = {item.provenance.record_id: item for item in incidents}

    _, found = grouped(incidents)

    family = found.families[0]
    assert family.family_id == family_id_for(family.event_ids)
    assert family.events == len(family.event_ids) == sum(family.per_month.values())
    assert family.incident_ids == sorted(item.incident_id for item in incidents)
    for reference in family.references:
        incident = by_record[reference.record_id]
        index = incident.evidence_by_id()
        assert reference.source_url == incident.provenance.source_url
        assert reference.when_basis == "event_time"
        assert reference.evidence_ids and len(reference.quotes) == len(reference.evidence_ids)
        for eid in reference.evidence_ids:
            row = index[eid]
            assert row.span_validation is SpanValidation.VALIDATED
            text = CONTENTS[reference.record_id]
            assert text[row.char_start:row.char_end] == row.quote


def test_a_flood_and_an_accident_in_one_village_are_never_shown_as_one_issue():
    pair = [a_flood(),
            report("NEWS-B", ACCIDENT_TA, kind="vehicle_accident", kind_quote="மோதியதில்",
                   place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்")]

    found = recurring_issues(pair, chain_factory=never, now=NOW)

    assert (found.buckets, found.asked, found.families) == (0, 0, [])
    assert found.units == 2


def test_two_different_tamil_wordings_of_one_water_problem_are_grouped_by_the_model():
    judge, found = grouped([supply(), well()])

    assert (found.buckets, found.asked) == (1, 1) and len(judge.asked) == 1
    family = found.families[0]
    assert family.review is ReviewState.ACCEPTED and family.findings == []
    assert family.incident_types == ["borewell_or_well_issue", "drinking_water_shortage"]
    assert family.categories == ["water"]
    assert family.events == 2 and family.geographic_precision == "village"


def test_the_word_water_does_not_group_a_shortage_with_a_canal_breach():
    found = recurring_issues([supply(), canal()], chain_factory=never, now=NOW)

    assert (found.buckets, found.families) == (0, [])
    assert found.units == 2


def test_the_model_keeping_events_apart_files_no_issue_rather_than_a_confident_one():
    _, found = grouped([supply(), well()], leaves_alone)

    assert (found.families, found.findings, found.asked) == ([], [], 1)
    assert len(found.alone) == 2
    assert any("matched nothing else" in line for line in found.limitations())


def test_one_event_alone_is_refused_as_recurrence_by_the_pipeline_and_by_the_model():
    found = recurring_issues([a_flood()], chain_factory=never, now=NOW)

    assert (found.units, found.buckets, found.families, found.asked) == (1, 0, [], 0)

    _, partial = grouped([a_flood(), b_flood()], grouping(events=[1]))

    assert partial.families == [] and len(partial.alone) == 2
    assert "establishes no recurrence" in partial.findings[0]

    _, accepted = grouped([flood("NEWS-C", "ஆகஸ்ட் 2 அன்று", "2026-08-02"),
                           flood("NEWS-D", "செப்டம்பர் 14 அன்று", "2026-09-14")])
    document = accepted.families[0].model_dump(mode="json")
    document["event_ids"] = document["event_ids"][:1]
    document["events"] = 1
    document["family_id"] = family_id_for(document["event_ids"])

    with pytest.raises(ValidationError, match="establishes no recurrence"):
        IssueFamily.model_validate(document)


def test_events_the_model_joined_only_through_a_third_event_are_held_for_review():
    outside = {"location": {"raw_text": None, "district": None, "state": "Tamil Nadu"}}
    a = note("NEWS-A", "கருங்காலக்குடி கிராமத்தில் ஆகஸ்ட் 2 அன்று வைகை ஆற்று வெள்ளம் 12 வீடுகள் "
                       "மூழ்கின",
             kind="flood", kind_quote="வைகை ஆற்று வெள்ளம்", place="Karungalakudi",
             place_quote="கருங்காலக்குடி கிராமத்தில்", location_fields={"village": "Karungalakudi"},
             when="2026-08-02T00:00:00", date_quote="ஆகஸ்ட் 2 அன்று", **outside)
    b = note("NEWS-B", "மேலூர் அருகே கருங்காலக்குடி கிராமத்தில் செப்டம்பர் 14 அன்று வைகை ஆற்று "
                       "வெள்ளம் 30 வீடுகள் மூழ்கின",
             kind="flood", kind_quote="வைகை ஆற்று வெள்ளம்", place="Karungalakudi",
             place_quote="கருங்காலக்குடி கிராமத்தில்",
             location_fields={"village": "Karungalakudi", "taluk": "மேலூர்"},
             when="2026-09-14T00:00:00", date_quote="செப்டம்பர் 14 அன்று", **outside)
    c = note("NEWS-C", "மேலூர் தாலுகாவில் அக்டோபர் 3 அன்று வைகை ஆற்று வெள்ளம் 8 வீடுகள் மூழ்கின",
             kind="flood", kind_quote="வைகை ஆற்று வெள்ளம்", place="மேலூர்",
             place_quote="மேலூர் தாலுகாவில்", location_type="taluk",
             location_fields={"taluk": "மேலூர்"},
             when="2026-10-03T00:00:00", date_quote="அக்டோபர் 3 அன்று", **outside)

    _, found = grouped([a, b, c], grouping(events=[1, 3]))

    assert found.buckets == 1 and found.families[0].events == 2
    family = found.families[0]
    assert family.review is ReviewState.REVIEW_REQUIRED
    assert any("only through a third event" in item for item in family.findings)
    assert len(found.alone) == 1


def test_a_family_spanning_two_categories_is_filed_for_review():
    _, found = grouped([flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02", category="disaster"),
                        flood("NEWS-B", "செப்டம்பர் 14 அன்று", "2026-09-14", category="agriculture")])

    family = found.families[0]
    assert family.categories == ["agriculture", "disaster"]
    assert family.review is ReviewState.REVIEW_REQUIRED
    assert any("spans categories" in item for item in family.findings)


@pytest.mark.parametrize(
    ("changes", "finding"),
    [
        ({"confidence": 0.4}, "is below 0.8"),
        ({"confidence": None}, "was used"),
        ({"evidence_ids": ["ev-invented"]}, "which was not shown to the model"),
        ({"department": "public_works"}, "not one the contract has"),
        ({"label": None}, "named no problem"),
        ({"why_separate": ["one names a shortage, the other a breach"]},
         "the model itself listed differences"),
    ],
)
def test_a_grouping_a_safeguard_questions_is_held_for_review_not_accepted(changes, finding):
    _, found = grouped([a_flood(), b_flood()], grouping(**changes))

    family = found.families[0]
    assert family.review is ReviewState.REVIEW_REQUIRED
    assert finding in "; ".join(family.findings)
    assert found.accepted == []
    assert found.counts()["Issues for review"] == 1


def test_a_date_window_counts_only_the_events_it_was_given():
    incidents = [a_flood(), b_flood(), flood("NEWS-C", "அக்டோபர் 3 அன்று", "2026-10-03")]

    _, windowed = grouped(incidents, filters=Filters(since=date(2026, 9, 1)))
    _, single = grouped(incidents, filters=Filters(since=date(2026, 10, 1)))

    assert (windowed.excluded_by_filter, windowed.units, windowed.families[0].events) == (1, 2, 2)
    assert "from 2026-09-01" in windowed.counts()["Filters"]
    assert any("outside the filters" in line for line in windowed.limitations())
    assert (single.excluded_by_filter, single.units, single.buckets, single.families) == (2, 1, 0, [])
    assert "no filter" not in single.counts()["Filters"]


@pytest.mark.parametrize(
    ("filters", "kept"),
    [
        (Filters(category=IncidentCategory.WATER), True),
        (Filters(category=IncidentCategory.DISASTER), False),
        (Filters(incident_type=EventType.DRINKING_WATER_SHORTAGE), True),
        (Filters(incident_type=EventType.FLOOD), False),
        (Filters(department=Department.WATER_RESOURCES), True),
        (Filters(department=Department.POLICE), False),
        (Filters(min_severity=SeverityLevel.MODERATE), True),
        (Filters(min_severity=SeverityLevel.HIGH), False),
        (Filters(place="கருங்காலக்குடி"), True),
        (Filters(place="Karungalakudi"), True),
        (Filters(place="Vadipatti"), False),
        (Filters(since=date(2026, 10, 1)), False),
        (Filters(until=date(2026, 9, 30)), True),
        (Filters(), True),
    ],
)
def test_each_filter_keeps_only_what_the_incident_itself_stated(filters, kept):
    assert filters.keeps(supply()) is kept


def test_an_unresolved_severity_and_a_missing_filter_are_both_honest():
    quiet = note("NEWS-QUIET", SUPPLY_TA, kind="drinking_water_shortage",
                 kind_quote="குடிநீர் தொட்டி நிரம்பவில்லை")

    assert quiet.severity.value == "unresolved"
    assert Filters(min_severity=SeverityLevel.INFO).keeps(quiet) is False
    assert Filters().describe() == "no filter"


def test_an_event_the_record_never_dated_is_outside_the_period_and_named_as_a_limitation():
    blank = undated(flood("NEWS-UNDATED", "ஆகஸ்ட் 2 அன்று", "2026-08-02"))
    assert incident_when(blank) == (None, "none")

    _, found = grouped([blank, b_flood()])

    assert (found.undated, found.units, found.buckets, found.families) == (1, 1, 0, [])
    assert any("carry no date" in line for line in found.limitations())


def test_affected_areas_stay_at_the_precision_the_records_named():
    taluk = note("NEWS-B", "மேலூர் தாலுகாவில் செப்டம்பர் 14 அன்று வைகை ஆற்று வெள்ளம் 40 வீடுகளை "
                           "மூழ்கடித்தது",
                 kind="flood", kind_quote="வைகை ஆற்று வெள்ளம்", place="மேலூர்",
                 place_quote="மேலூர் தாலுகாவில்", location_type="taluk",
                 location_fields={"taluk": "மேலூர்"},
                 when="2026-09-14T00:00:00", date_quote="செப்டம்பர் 14 அன்று")

    _, found = grouped([a_flood(), taluk])

    family = found.families[0]
    assert family.geographic_precision == "taluk"
    assert [(area.level, area.name) for area in family.areas] == [
        ("taluk", "மேலூர்"), ("village", "Sholavandan")]
    document = json.dumps(family.model_dump(mode="json"))
    assert "latitude" not in document and "canonical_location_id" not in document


def test_a_reading_that_invented_a_village_is_left_out_of_every_count():
    invented = flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02",
                     location_fields={"village": "Sholavandan", "taluk": "Vadipatti"})
    assert invented.review_required

    _, found = grouped([invented, b_flood()])

    assert (found.skipped_review, found.eligible, found.units, found.families) == (1, 1, 1, [])
    assert any("waiting for a person" in line for line in found.limitations())


def test_a_change_over_time_is_measured_only_across_two_halves_of_a_wide_period():
    _, compared = grouped([flood("NEWS-A", "ஆகஸ்ட் 1 அன்று", "2026-08-01"),
                           flood("NEWS-B", "செப்டம்பர் 20 அன்று", "2026-09-20"),
                           flood("NEWS-C", "அக்டோபர் 5 அன்று", "2026-10-05")])
    family = compared.families[0]

    assert family.change == 1 and family.comparison is None
    assert (family.recent.events, family.previous.events) == (2, 1)
    assert (family.recent.since, family.recent.until) == (date(2026, 9, 2), date(2026, 10, 5))
    assert family.sum_of_months() == family.events == 3

    _, narrow = grouped([flood("NEWS-D", "ஆகஸ்ட் 1 அன்று", "2026-08-01"),
                         flood("NEWS-E", "ஆகஸ்ட் 20 அன்று", "2026-08-20")])
    assert narrow.families[0].change is None
    assert "shorter than the 61 days" in narrow.families[0].comparison


def test_more_events_than_one_request_holds_are_shown_latest_first_and_said_to_be_cut():
    incidents = [
        flood(f"NEWS-{index:02d}", f"அக்டோபர் {index} அன்று", f"2026-10-{index:02d}")
        for index in range(1, 11)
    ]

    judge, found = grouped(incidents)

    assert (found.buckets, found.asked, found.truncated) == (1, 1, 2)
    assert found.families[0].events == MAX_EVENTS_PER_BUCKET
    assert found.families[0].review is ReviewState.REVIEW_REQUIRED
    assert any("were not shown" in item for item in found.families[0].findings)
    assert "group only what is printed" in judge.asked[0].user
    assert any("may be smaller than the problem" in line for line in found.limitations())


@pytest.mark.parametrize(
    ("status", "where"),
    [
        ("action_taken", "recorded"),
        ("resolved", "recorded"),
        ("ongoing", "followups"),
        ("under_investigation", "followups"),
        ("reported", "followups"),
        ("unknown", "nowhere"),
    ],
)
def test_a_status_reaches_a_family_only_when_the_record_s_own_words_support_it(status, where):
    _, found = grouped([flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02", status=status,
                              status_quote=CLEANED),
                        b_flood()])
    family = found.families[0]
    mine = [row for row in [*family.recorded, *family.followups] if row.record_id == "NEWS-A"]

    assert len(mine) == (0 if where == "nowhere" else 1)
    for row in mine:
        assert (row.status, row.quote) == (status, CLEANED)
        assert row.evidence_ids and row.retrieved_at is not None
        assert row.source_url == "https://news.example.test/NEWS-A"
    assert (len(family.recorded) > 0) is (where == "recorded")


def test_a_recorded_action_and_a_suggested_one_are_kept_in_different_places():
    _, found = grouped([flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02", status="action_taken",
                              status_quote=CLEANED),
                        b_flood()])
    family = found.families[0]
    document = family.model_dump(mode="json")

    assert [row.status for row in family.recorded] == ["action_taken"]
    assert family.recorded[0].quote == CLEANED
    assert len(family.suggested) == 1
    suggestion = family.suggested[0]
    assert (suggestion.recorded, suggestion.method) == (False, ExtractionMethod.LLM)
    assert suggestion.department is Department.WATER_RESOURCES
    assert suggestion.event_ids == family.event_ids
    assert CLEANED not in suggestion.action
    assert document["suggested"][0]["recorded"] is False
    assert "never a record of work assigned" in "; ".join(found.limitations())


def test_an_empty_or_unreadable_dataset_reports_nothing_rather_than_a_quiet_zero():
    stream = io.StringIO()
    found = recurring_issues([], chain_factory=never, now=NOW)

    print_issues(found, stream=stream)
    printed = stream.getvalue()

    assert (found.eligible, found.units, found.buckets, found.families, found.asked) == (
        0, 0, 0, [], 0)
    assert "Recurring issues (issue-1" in printed
    assert "Recurring issues: 0" in printed and "Accepted recurring issues: 0" in printed
    assert "never a record of work assigned" in printed

    waiting = recurring_issues([flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02",
                                      location_fields={"taluk": "Vedur"})],
                               chain_factory=never, now=NOW)

    assert (waiting.eligible, waiting.units, waiting.families, waiting.asked) == (0, 0, [], 0)
    assert waiting.skipped_review == 1


def test_a_model_that_refuses_and_one_that_answers_nonsense_are_both_failures():
    def refuse(request: LLMRequest) -> dict[str, Any]:
        raise LLMError("the endpoint said no")

    _, failed = grouped([a_flood(), b_flood()], refuse)

    assert failed.families == [] and failed.asked == 0 and len(failed.failed) == 1
    assert "the endpoint said no" in failed.failed[0]
    assert failed.counts()["Recurring issues"] == 0

    def nonsense(request: LLMRequest) -> dict[str, Any]:
        return {"families": [{"events": "all of them"}]}

    _, unusable = grouped([a_flood(), b_flood()], nonsense)

    assert unusable.families == [] and "is not a grouping" in unusable.failed[0]

    def exhausted(request: LLMRequest) -> dict[str, Any]:
        raise QuotaExhaustedError("daily tokens used up")

    _, deferred = grouped([a_flood(), b_flood()], exhausted)

    assert deferred.stop_reason and "daily tokens" in deferred.stop_reason
    assert deferred.not_grouped == 1 and deferred.families == []


def test_a_grouping_is_reused_until_the_events_or_the_prompt_change(tmp_path):
    incidents = [a_flood(), b_flood()]

    _, first = grouped(incidents, store=IssueStore(tmp_path).load())
    judge, second = grouped(incidents, store=IssueStore(tmp_path).load())

    assert (first.asked, first.reused) == (1, 0)
    assert (second.asked, second.reused) == (0, 1) and judge.asked == []
    assert [family.family_id for family in second.families] == [
        family.family_id for family in first.families]
    saved = (tmp_path / ISSUES_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(saved) == 1
    summary = json.loads((tmp_path / RECURRING_FILENAME).read_text(encoding="utf-8"))
    assert sorted(summary) == sorted(
        ("schema_version", "prompt_version", "prompt_digest", "filters", "counts",
         "limitations", "issues"))
    assert summary["prompt_digest"] == ISSUE_PROMPT_DIGEST

    stale = json.loads(saved[0])
    stale["prompt_digest"] = "0" * 16
    (tmp_path / ISSUES_FILENAME).write_text(json.dumps(stale) + "\n", encoding="utf-8")

    judge, again = grouped(incidents, store=IssueStore(tmp_path).load())

    assert (again.asked, again.reused) == (1, 0) and len(judge.asked) == 1

    wider = [*incidents, flood("NEWS-C", "அக்டோபர் 3 அன்று", "2026-10-03")]
    judge, grown = grouped(wider, store=IssueStore(tmp_path).load())

    assert (grown.asked, grown.reused) == (1, 0) and grown.families[0].events == 3
    assert len(judge.asked) == 1


def test_a_damaged_issues_file_stops_the_run_instead_of_being_overwritten(tmp_path):
    (tmp_path / ISSUES_FILENAME).write_text('{"family_id": "ISS-x"}\n', encoding="utf-8")

    with pytest.raises(CheckpointError, match="line 1 is not a grouping this pipeline wrote"):
        IssueStore(tmp_path).load()
    assert (tmp_path / ISSUES_FILENAME).read_text(encoding="utf-8") == '{"family_id": "ISS-x"}\n'


def test_the_report_prints_the_groups_the_areas_and_marks_the_suggestion_as_a_suggestion():
    stream = io.StringIO()
    _, found = grouped([flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02", status="action_taken",
                              status_quote=CLEANED),
                        b_flood()])

    print_issues(found, detailed=True, stream=stream)
    printed = stream.getvalue()

    assert "Recurring issues: 1" in printed and "Accepted recurring issues: 1" in printed
    assert "distinct event(s) over 43 day(s)" in printed
    assert "areas: Sholavandan (village)" in printed
    assert "per month: 2026-08=1, 2026-09=1" in printed
    assert f"recorded by NEWS-A: action_taken - {CLEANED}" in printed
    assert "suggested for water_resources: Test the supply" in printed
    assert printed.index("recorded by") < printed.index("(suggested, not recorded)")
    assert "change: not compared" in printed


def test_the_pipeline_groups_recurring_issues_and_reuses_them_next_run(tmp_path, capsys):
    records = [
        waterlogging(),
        waterlogging(record_id="NEWS-MDU-WLR-0002",
                     source_url="https://www.dinamalar.com/news/madurai/rain/4338981"),
    ]

    def answer(request: LLMRequest) -> dict[str, Any]:
        if "bucket_id" in request.meta:
            return grouping()(request)
        return water_answer()

    provider = Answering(answer)
    argv = ["--all", "--recurring-issues", "--state-dir", str(tmp_path)]

    code = main(argv, provider=provider, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "Recurring issues (issue-1" in out
    assert "Candidate groups: 1" in out and "Grouped now: 1" in out
    assert "Recurring issues: 1" in out and "Accepted recurring issues: 1" in out
    assert len([request for request in provider.asked if "bucket_id" in request.meta]) == 1

    second = Answering(answer)
    code = main(argv, provider=second, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "Reused decisions: 1" in out and "Grouped now: 0" in out
    assert not [request for request in second.asked if "bucket_id" in request.meta]


def test_the_pipeline_counts_only_events_a_saved_link_decision_merged(tmp_path, capsys):
    records = [
        waterlogging(),
        waterlogging(record_id="NEWS-MDU-WLR-0002",
                     source_url="https://www.dinamalar.com/news/madurai/rain/4338981"),
    ]

    def judge_only(request: LLMRequest) -> dict[str, Any]:
        if "link_id" in request.meta:
            return verdict()(request)
        return water_answer()

    linked = Answering(judge_only)
    code = main(["--all", "--link-events", "--state-dir", str(tmp_path)],
                provider=linked, environ={}, transport=responder(listing(*records)))
    assert code == EXIT_OK, capsys.readouterr().out

    def nothing_left(request: LLMRequest) -> dict[str, Any]:
        raise AssertionError("one distinct event is no recurrence, so nothing was asked")

    quiet = Answering(nothing_left)
    code = main(["--all", "--recurring-issues", "--state-dir", str(tmp_path)],
                provider=quiet, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "Distinct events counted: 1" in out and "Recurring issues: 0" in out
    assert "no link decisions were available" not in out
    assert quiet.asked == []


def test_a_recurring_filter_without_the_flag_and_a_reversed_window_are_refused(capsys):
    for argv in (["--all", "--place", "Madurai"],
                 ["--all", "--category", "water"],
                 ["--all", "--recurring-issues", "--since", "2026-10-05",
                  "--until", "2026-10-01"]):
        with pytest.raises(SystemExit):
            main(argv, environ={})
        assert "usage:" in capsys.readouterr().err
