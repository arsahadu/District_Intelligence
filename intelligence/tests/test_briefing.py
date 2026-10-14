from __future__ import annotations

import io
import json
import re
from datetime import date
from typing import Any, Callable, Optional, Sequence

import pytest
from pydantic import ValidationError

from intelligence.briefing import (
    BRIEF_PROMPT_DIGEST,
    BRIEF_PROMPT_VERSION,
    BRIEF_RULES,
    BRIEF_SCHEMA,
    BRIEF_SUMMARY_FILENAME,
    BRIEF_THRESHOLD,
    BRIEFINGS_FILENAME,
    DEFAULT_BRIEF_DAYS,
    IDENTIFIERS,
    MAX_BRIEF_ACTIONS,
    MAX_BRIEF_DAYS,
    MAX_BRIEF_EVENTS,
    MAX_BRIEF_LINES,
    NOTHING_READ,
    PAST_WORDS,
    WHOLE_CORPUS,
    ActionKind,
    ActionOrigin,
    BriefAction,
    BriefDraft,
    BriefItemKind,
    BriefItemRef,
    BriefLanguage,
    BriefLine,
    BriefReport,
    BriefSection,
    BriefState,
    BriefStore,
    Briefing,
    Measured,
    brief_id_for,
    brief_lines,
    brief_request,
    briefing_document,
    carried_actions,
    district_briefing,
    item_refs,
    measure,
    measured_digits,
    overlapped,
    past_claims_in,
    print_briefings,
    rules_for,
    script_mismatch,
    window_note,
)
from intelligence.checkpoint import CheckpointError
from intelligence.contract import Department, Incident, IncidentCategory, RecordKind, ReviewState
from intelligence.copilot import DEFAULT_CONFIDENCE, MIN_QUOTED_EVENTS
from intelligence.fallback import Chain
from intelligence.llm import LLMError, LLMProvider, LLMRequest, QuotaExhaustedError
from intelligence.pipeline import (
    EXIT_CHECKPOINT,
    EXIT_DEFERRED,
    EXIT_OK,
    EXIT_PARTIAL,
    main,
)
from intelligence.recurrence import Filters
from intelligence.tests.builders import listing, responder, water_answer
from intelligence.tests.record_fixtures import market_price, weather_forecast, waterlogging
from intelligence.tests.test_linking import NOW, Answering, never
from intelligence.tests.test_recurrence import flood, grouped, grouping, undated, well
from intelligence.tests.test_recurrence import supply as water_supply
from intelligence.tests.test_trends import RECENT, merged, reading, steady, thicker
from intelligence.structured import process_record
from intelligence.trends import NOT_ASKED, TrendStatus, emerging_trends

DAYS = 70
SUPPLY_ID = "NEWS-SUPPLY"
WELL_ID = "NEWS-WELL"
TA_LINE = "ஒரே ஊரில் இந்த் பிரச்சனை மீண்டும் மீண்டும் பதிவாகியுளளது"


def three() -> list[Incident]:
    return [
        flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02"),
        flood("NEWS-B", "செப்டம்பர் 14 அன்று", "2026-09-14"),
        flood("NEWS-C", "செப்டம்பர் 28 அன்று", "2026-09-28"),
    ]


def pair() -> list[Incident]:
    return [water_supply(record_id=SUPPLY_ID), well(record_id=WELL_ID)]


def grouped_pair() -> list[Any]:
    _, found = grouped(pair(), grouping())
    return found.families


def trended() -> tuple[list[Incident], list[Any], list[Any]]:
    incidents = steady()
    links = merged(incidents).links
    found = emerging_trends(
        incidents, chain_factory=lambda: Chain.single(Answering(reading())), links=links, now=NOW
    )
    return incidents, links, found.trends


def seen_of(request: LLMRequest) -> dict[str, Any]:
    per: dict[int, list[str]] = {}
    index = 0
    for line in request.user.splitlines():
        header = re.match(r"^Event (\d+) \(", line)
        if header:
            index = int(header.group(1))
            per[index] = []
            continue
        cited = re.match(r"^\[([^\]]+)\]", line)
        if cited and index:
            per[index].append(cited.group(1))
    return {
        "events": sorted(per),
        "evidence": per,
        "quoted": [rows[0] for rows in per.values() if rows],
        "saved": sorted(set(re.findall(r"\b(?:ISS|TRD)-[0-9a-f]{16}\b", request.user))),
        "user": request.user,
    }


def situation(events: list[int], evidence: list[str], **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "section": "situation",
        "body": "This keeps happening in the same village.",
        "events": events,
        "saved": [],
        "evidence": evidence,
        "confidence": 0.9,
    }
    return body | changes


def follow_up(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": "Check the supply line before the next failure.",
        "kind": "follow_up",
        "section": "situation",
        "target_event": 1,
        "department": None,
        "events": [1],
        "evidence": [],
        "why": "The records state no answer yet.",
        "confidence": 0.9,
    }
    return body | changes


def drafting(**changes: Any) -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        if request.meta.get("prompt_version") != BRIEF_PROMPT_VERSION:
            raise AssertionError(f"not a briefing request: {request.meta}")
        seen = seen_of(request)
        body: dict[str, Any] = {
            "headline": "One problem kept returning in one village",
            "language": "english",
            "lines": [situation(seen["events"], seen["quoted"])],
            "actions": [],
            "caveats": [],
            "saved": [],
            "confidence": 0.9,
        }
        for key, value in changes.items():
            body[key] = value(seen) if callable(value) else value
        return body

    return answer


def asked(
    incidents: Optional[Any] = None,
    body: Optional[Callable[[LLMRequest], dict[str, Any]]] = None,
    **fields: Any,
) -> tuple[Answering, BriefReport]:
    if callable(incidents):
        incidents, body = None, incidents
    judge = Answering(body or drafting())
    fields.setdefault("days_back", DAYS)
    found = district_briefing(
        three() if incidents is None else incidents,
        chain_factory=lambda: Chain.single(judge),
        now=NOW,
        **fields,
    )
    return judge, found


def one(report: BriefReport) -> Briefing:
    assert len(report.briefings) == 1
    return report.briefings[0]


def silent(incidents: Sequence[Incident], **fields: Any) -> BriefReport:
    fields.setdefault("days_back", DAYS)
    return district_briefing(
        incidents, chain_factory=never, now=NOW, **fields
    )


def test_the_briefing_prompt_is_pinned_and_puts_every_measure_before_the_model():
    assert BRIEF_PROMPT_VERSION == "brief-1"
    assert BRIEF_PROMPT_DIGEST == "0b6f0fefb6212216"
    for phrase in (
        "Every count, total, date, window, place list and status printed was measured from the records",
        "Never write a number that is not printed above",
        "Nothing in these records says a suggestion was carried out",
        "Never write that an action was done, is being done, was ordered or was instructed",
        "A measured increase is alert-eligible or potential exactly as printed",
        "A line or an action you cannot cite is not written",
        "never assign one from the kind of problem",
        "Never write that something is confirmed, proved, statistically significant",
        "Do not stretch a nearby fact into a finding",
    ):
        assert phrase in BRIEF_RULES
    assert set(BRIEF_SCHEMA["properties"]) == {
        "headline",
        "language",
        "lines",
        "actions",
        "caveats",
        "saved",
        "confidence",
    }


def test_the_request_prints_only_what_the_pipeline_measured():
    judge, report = asked()
    user = judge.asked[0].user
    assert judge.asked[0].meta == {"prompt_version": "brief-1", "period": "2026-07-21..2026-09-28"}
    assert (
        "Period: 2026-07-21..2026-09-28 - 70 day(s) counted back from 2026-09-28, the newest dated "
        "record here (its date read from event_time)." in user
    )
    assert (
        "The period holds 3 distinct event(s) from 3 accepted incident(s) in 3 record(s) filed by 1 "
        "publisher(s)." in user
    )
    assert "Places in the period: Sholavandan (village, 3 event(s)), at village precision." in user
    assert "Sections with content here: situation, open" in user
    assert "Event types here: flood" in user
    assert "All 3 event(s) of the period are printed here." in user
    assert (
        "Every count, window, place list and status above was measured by the pipeline from the records, "
        "never asked of you." in user
    )
    assert "Write headline, every line body and every action in english." in user
    assert "Event 1 (EVT-" in user and "Event 3 (EVT-" in user
    assert one(report).state is BriefState.ISSUED


def test_the_rules_name_the_language_asked_and_never_a_second_one():
    for language, wanted in ((BriefLanguage.ENGLISH, "in english."), (BriefLanguage.TAMIL, "in tamil.")):
        said = rules_for(language)
        assert wanted in said
        assert "LANGUAGE" not in said
    assert BriefSection.RISING.value in rules_for(BriefLanguage.ENGLISH)
    assert ActionKind.MONITOR.value in rules_for(BriefLanguage.ENGLISH)
    assert Department.WATER_RESOURCES.value in rules_for(BriefLanguage.ENGLISH)


def test_a_run_that_read_nothing_asks_no_model_and_withholds_the_briefing():
    report = silent([])
    brief = one(report)
    assert brief.state is BriefState.WITHHELD
    assert brief.review is ReviewState.REVIEW_REQUIRED
    assert brief.missing == NOTHING_READ
    assert brief.lines == [] and brief.actions == []
    assert brief.provider == NOT_ASKED and brief.model == NOT_ASKED
    assert report.counts()["Distinct events"] == 0
    assert report.limitations()[0] == NOTHING_READ


def test_the_window_is_counted_back_from_the_newest_dated_record_and_says_so():
    _, report = asked()
    brief = one(report)
    assert (brief.since, brief.until) == (date(2026, 7, 21), date(2026, 9, 28))
    assert brief.days_back == DAYS
    assert brief.window_basis == "record"
    assert brief.anchor.date() == date(2026, 9, 28)
    assert brief.anchor_basis == "event_time"
    document = briefing_document(brief)
    assert document[1].startswith("Period: 2026-07-21..2026-09-28 (70 day(s) counted back from")
    assert DEFAULT_BRIEF_DAYS == 7 and MAX_BRIEF_DAYS == 365


def test_a_period_the_run_named_starts_where_it_said_and_counts_no_days_back():
    _, report = asked(filters=Filters(since=date(2026, 9, 1)))
    brief = one(report)
    assert (brief.since, brief.until, brief.days_back) == (date(2026, 9, 1), date(2026, 9, 28), None)
    assert brief.window_basis == "named"
    assert window_note("named", None, brief.until, brief.anchor_basis) == (
        "the period starts where the run named it"
    )


def test_a_run_told_where_the_period_ends_counts_its_days_from_there():
    judge, report = asked(filters=Filters(until=date(2026, 9, 20)), days_back=10)
    brief = one(report)
    assert (brief.since, brief.until, brief.window_basis) == (
        date(2026, 9, 11),
        date(2026, 9, 20),
        "given",
    )
    assert "where the run was told the period ends" in briefing_document(brief)[1]
    assert brief.events == 1
    assert "2026-09-14" in judge.asked[0].user and "2026-09-28" not in judge.asked[0].user


def test_a_period_with_no_record_dated_in_it_withholds_rather_than_claiming_a_zero():
    _, report = asked(filters=Filters(since=date(2027, 1, 1)))
    brief = one(report)
    assert brief.state is BriefState.WITHHELD
    assert brief.events == 0
    assert "so this period had nothing to count" in str(brief.missing)


def test_an_undated_event_falls_in_no_period_and_is_named_as_one():
    incidents = [*three(), undated(flood("NEWS-D", "நேற்று", "2026-09-20"))]
    judge, report = asked(incidents)
    brief = one(report)
    assert brief.undated == 1
    assert "1 distinct event(s) in these records carry no date, so they fall in no period." in (
        judge.asked[0].user
    )
    assert any("carry no date the record gave" in line for line in report.limitations())


def test_an_incident_waiting_for_a_person_leaves_every_count_and_says_so():
    held = waiting(flood("NEWS-HELD", "அக்டோபர் 5 அன்று", "2026-10-05"))
    judge, report = asked([*three(), held])
    brief = one(report)
    assert (report.skipped_review, brief.skipped_review) == (1, 1)
    assert held.incident_id not in brief.incident_ids
    assert (brief.incidents, brief.events) == (3, 3)
    assert brief.until == date(2026, 9, 28)
    assert report.counts()["Left out (waiting for a person)"] == 1
    assert any(
        "1 incident(s) waiting for a person were left out of every count" in line
        for line in report.limitations()
    )


def test_a_forecast_or_a_price_line_in_the_records_is_never_briefed_from():
    others = [process_record(weather_forecast()), process_record(market_price())]
    _, report = asked([*three(), *others])
    brief = one(report)
    assert all(item.record_kind is not RecordKind.INCIDENT for item in others)
    assert not any(item.incident_id in brief.incident_ids for item in others)
    assert (brief.incidents, brief.records, brief.events) == (3, 3, 3)
    assert brief.until == date(2026, 9, 28)
    assert report.failed == []


def waiting(incident: Incident) -> Incident:
    copy = incident.model_copy(deep=True)
    copy.validation.state = ReviewState.REVIEW_REQUIRED
    copy.validation.issues = []
    return copy


def test_repeated_reports_the_linker_merged_are_counted_as_one_event():
    incidents, links, trends = trended()
    judge, report = asked(incidents, links=links, days_back=95)
    brief = one(report)
    assert (brief.events, brief.incidents) == (5, 9)
    assert report.deduplicated and report.links == 6
    assert "Repeated reports of one happening were merged into one event before counting." in (
        judge.asked[0].user
    )
    assert not any("no link decisions were available" in line for line in report.limitations())


def test_without_link_decisions_a_count_says_it_may_double_what_happened():
    judge, report = asked(steady(), days_back=95)
    assert report.deduplicated is False
    assert report.limitations()[0].startswith("no link decisions were available")
    assert (
        "No link decisions were available, so two reports of one happening may be printed as two events "
        "and a count may double what happened." in judge.asked[0].user
    )
    assert (
        "Repeated reports of one happening were merged into one event before counting."
        not in judge.asked[0].user
    )


def test_more_events_than_one_request_holds_are_cut_and_the_briefing_says_so():
    _, report = asked(thicker(), days_back=120)
    brief = one(report)
    assert len(brief.event_ids) == 11
    assert brief.shown_events == MAX_BRIEF_EVENTS == 6
    assert brief.truncated_events == 5
    assert brief.state is BriefState.QUALIFIED
    assert "printed 6, so this briefing names fewer events than it counted" in " ".join(brief.findings)
    assert any(
        "beyond the 6 printed in one request" in line for line in report.limitations()
    )


def test_a_filter_that_excludes_every_incident_says_how_many_were_read():
    counts = measure([], [], Filters(category=IncidentCategory.WATER), 7, read=5)
    assert counts.empty_reason is not None
    assert counts.empty_reason.startswith("none of the 5 accepted incident(s) the run read matches")
    assert "category water" in counts.empty_reason
    report = silent([])
    assert report.briefings[0].missing == NOTHING_READ


def test_a_filter_narrowing_by_category_leaves_nothing_to_group():
    counts = measure(three(), [], Filters(category=IncidentCategory.WATER), 7)
    assert counts.units == []
    assert counts.empty_reason == (
        "no accepted incident matches category water, so there was nothing to group into events"
    )


def test_an_empty_period_outside_the_filters_counts_the_incidents_it_left_out():
    _, report = asked(three(), filters=Filters(place="Nowheretonamed"))
    brief = one(report)
    assert brief.state is BriefState.WITHHELD
    assert report.excluded_by_filter == 3
    assert any("3 incident(s) fell outside the filters used" in line for line in report.limitations())


def test_the_whole_corpus_is_named_when_no_record_carries_a_date():
    incidents = [undated(flood("NEWS-A", "நேற்று", "2026-08-02"))]
    _, report = asked(incidents)
    brief = one(report)
    assert brief.period() == WHOLE_CORPUS
    assert brief.since is None and brief.until is None and brief.window_basis == "none"
    assert window_note("none", None, None, "none") == (
        "no record here carried a date, so no window could be counted"
    )


def test_a_briefing_id_is_the_period_and_the_saved_items_it_prints():
    _, first = asked()
    _, again = asked()
    assert one(first).briefing_id == one(again).briefing_id
    other = brief_id_for(
        date(2026, 7, 21), date(2026, 9, 28), DAYS, BriefLanguage.TAMIL, one(first).event_ids, []
    )
    assert other != one(first).briefing_id
    assert one(first).briefing_id.startswith("BRF-")
    with pytest.raises(ValidationError, match="is not the one its period and items derive"):
        payload = one(first).model_dump(mode="json")
        payload["briefing_id"] = "BRF-" + "0" * 16
        Briefing.model_validate(payload)


def test_a_saved_recurring_issue_of_the_period_is_printed_as_an_item():
    families = grouped_pair()
    judge, report = asked(pair(), families=families, links=[], days_back=30)
    brief = one(report)
    assert [item.kind for item in brief.items] == [BriefItemKind.RECURRING_ISSUE]
    item = brief.items[0]
    assert item.item_id.startswith("ISS-") and item.alert_eligible is None
    assert (item.events, len(item.event_ids)) == (2, 2)
    assert item.evidence_ids and set(item.evidence_ids) <= {row.evidence_id for row in brief.citations}
    assert "Sections with content here: situation, recurring, open" in judge.asked[0].user
    assert f"Saved recurring issue {item.item_id}" in judge.asked[0].user
    assert brief.state is BriefState.ISSUED


def test_a_measured_increase_states_the_eligibility_the_measurement_reached():
    incidents, links, trends = trended()
    _, report = asked(incidents, links=links, measured=trends, days_back=95)
    brief = one(report)
    assert {item.kind for item in brief.items} == {BriefItemKind.MEASURED_INCREASE}
    assert all(item.alert_eligible is True for item in brief.items)
    assert report.alerts == 2
    document = "\n".join(briefing_document(brief))
    assert "(alert-eligible)" in document
    assert "not yet alert-eligible" not in document


def test_a_potential_increase_is_printed_as_one():
    incidents, links, trends = trended()
    measured = [
        trend.model_copy(update={"status": TrendStatus.POTENTIAL}) for trend in trends[:1]
    ]
    _, report = asked(incidents, links=links, measured=measured, days_back=95)
    brief = one(report)
    assert brief.items[0].alert_eligible is False
    assert "(not yet alert-eligible)" in "\n".join(briefing_document(brief))
    assert report.alerts == 0


def test_an_issue_whose_events_are_all_outside_the_period_prints_no_item():
    families = grouped_pair()
    _, report = asked(three(), families=families, days_back=DAYS)
    brief = one(report)
    assert brief.items == []
    assert "Saved recurring issues in the period" in json.dumps(report.counts())
    assert report.counts()["Saved recurring issues in the period"] == 0
    assert any("no saved recurring issue overlapped the period" in line for line in report.limitations())


def test_a_saved_issue_waiting_for_a_person_is_never_used_as_a_briefing_item():
    held = [
        family.model_copy(update={"review": ReviewState.REVIEW_REQUIRED})
        for family in grouped_pair()
    ]
    assert held[0].review is ReviewState.REVIEW_REQUIRED
    _, report = asked(pair(), families=held, days_back=30)
    brief = one(report)
    assert brief.items == []
    assert brief.actions == []
    assert report.families == 0
    assert any("no saved recurring issue overlapped the period" in line for line in report.limitations())


def test_a_suggested_action_the_saved_issue_wrote_is_carried_and_labels_its_origin():
    families = grouped_pair()
    _, report = asked(pair(), families=families, days_back=30)
    brief = one(report)
    carried = [item for item in brief.actions if item.origin is ActionOrigin.SAVED_ISSUE_FAMILY]
    assert len(carried) == 1
    action = carried[0]
    assert action.kind is None
    assert action.target_kind == BriefItemKind.RECURRING_ISSUE.value
    assert action.target_id == brief.items[0].item_id
    assert action.department is Department.WATER_RESOURCES
    assert action.recorded is False
    assert "as the saved issue wrote it" in "\n".join(briefing_document(brief))


def test_a_saved_action_that_claims_work_was_done_is_not_carried():
    families = grouped_pair()
    done = [
        family.model_copy(
            update={
                "suggested": [
                    item.model_copy(update={"action": "The line was already repaired."})
                    for item in family.suggested
                ]
            }
        )
        for family in families
    ]
    _, report = asked(pair(), families=done, days_back=30)
    brief = one(report)
    assert [item.origin for item in brief.actions] != [ActionOrigin.SAVED_ISSUE_FAMILY]
    assert any(
        "says something was done or writes an unmeasured number, so it is not carried" in line
        for line in brief.findings
    )


def test_a_saved_action_whose_words_no_record_of_this_period_supports_is_not_carried():
    orphan = [
        family.model_copy(
            update={
                "suggested": [
                    item.model_copy(update={"evidence_ids": []}) for item in family.suggested
                ]
            }
        )
        for family in grouped_pair()
    ]
    assert orphan[0].suggested[0].evidence_ids == []
    _, report = asked(pair(), families=orphan, days_back=30)
    brief = one(report)
    assert brief.actions == []
    assert any(
        "cites no quotation this briefing counts, so it is not carried" in line
        for line in brief.findings
    )
    assert brief.items and brief.items[0].evidence_ids


def test_a_line_naming_an_event_that_was_never_printed_is_left_out():
    _, report = asked(drafting(lines=lambda seen: [situation([99], [])]))
    brief = one(report)
    assert brief.lines == []
    assert any(
        "names event 99, which was not printed for it" in line for line in brief.findings
    )
    assert any(
        "the situation line names nothing it follows from, so it is left out" in line
        for line in brief.findings
    )
    assert brief.state is BriefState.WITHHELD


def test_an_action_following_an_unprinted_event_is_left_out_entirely():
    _, report = asked(
        drafting(
            lines=lambda seen: [],
            actions=lambda seen: [follow_up(target_event=42, events=seen["events"])],
        )
    )
    brief = one(report)
    assert brief.actions == []
    assert any("follows event 42, which was not printed for it" in line for line in brief.findings)


def test_an_action_naming_nothing_it_follows_from_is_refused_as_a_general_instruction():
    _, report = asked(
        drafting(
            lines=lambda seen: [],
            actions=lambda seen: [follow_up(target_event=None, events=seen["events"])],
        )
    )
    brief = one(report)
    assert brief.actions == []
    assert any("names nothing it follows from, so it is left out" in line for line in brief.findings)
    with pytest.raises(ValidationError, match="it is a general instruction"):
        BriefAction(
            action="Improve the drainage of the district.",
            kind=ActionKind.OTHER,
            origin=ActionOrigin.BRIEFING,
            confidence=0.9,
            review=ReviewState.ACCEPTED,
            evidence_ids=["ev-A"],
        )


def test_a_line_citing_a_quotation_that_was_never_printed_is_held_not_accepted():
    _, report = asked(drafting(lines=lambda seen: [situation(seen["events"], ["ev-INVENTED-0000"])]))
    brief = one(report)
    assert len(brief.lines) == 1
    assert brief.lines[0].review is ReviewState.REVIEW_REQUIRED
    assert any(
        "which is not one of the quotations printed with the records it was shown" in line
        for line in brief.findings
    )
    assert brief.state is BriefState.QUALIFIED


def test_a_number_the_records_never_measured_is_left_out_of_every_field():
    for field, text in (
        ("lines", None),
        ("headline", "Floodwater damaged 91 houses again"),
        ("caveats", ["91 houses were reported in this period."]),
    ):
        if text is None:
            _, report = asked(
                drafting(
                    lines=lambda seen: [
                        situation(seen["events"], seen["quoted"], body="91 houses were damaged here.")
                    ]
                )
            )
        else:
            _, report = asked(drafting(**{field: text}))
        brief = one(report)
        assert "91" not in brief.numbers
        assert any("a number no measured line printed" in line for line in brief.findings)
        assert all("91" not in str(item) for item in brief.lines)
        if field != "lines":
            assert "91" not in " ".join([str(brief.headline)] + brief.caveats)


def test_digits_inside_a_saved_identifier_are_never_measured_numbers():
    line = (
        "Measured increase TRD-54dbb7c268e298e5 holds 3 distinct event(s) in 2026-09-11..2026-10-10 "
        "against a baseline average of 1.00"
    )
    assert measured_digits([line]) == {"3", "2026", "9", "11", "10", "1", "0"}
    assert not {"268", "298", "54", "5", "7"} & measured_digits([line])
    assert IDENTIFIERS.sub("", "ISS-2db30aa3eb2dfb3f counts") == " counts"


def test_a_line_that_copies_digits_out_of_an_item_identifier_is_left_out():
    incidents, links, trends = trended()
    _, report = asked(
        incidents,
        links=links,
        measured=trends,
        days_back=95,
        body=drafting(
            lines=lambda seen: [
                situation(
                    seen["events"],
                    seen["quoted"],
                    section="rising",
                    body="The 268 reports measured here keep rising.",
                )
            ]
        ),
    )
    brief = one(report)
    assert brief.lines == []
    assert any(
        "a rising line writes 268, a number no measured line printed" in line for line in brief.findings
    )


def test_a_section_the_period_measured_nothing_for_cannot_be_written():
    _, report = asked(drafting(lines=lambda seen: [situation(seen["events"], seen["quoted"], section="documents")]))
    brief = one(report)
    assert brief.lines == []
    assert any(
        "a documents line was written although nothing was measured for that section" in line
        for line in brief.findings
    )


def test_a_section_or_kind_outside_the_tokens_it_was_given_is_named_as_one():
    _, report = asked(
        drafting(
            lines=lambda seen: [situation(seen["events"], seen["quoted"], section="gossip")],
            actions=lambda seen: [follow_up(kind="directive", events=seen["events"], evidence=seen["quoted"])],
        )
    )
    brief = one(report)
    assert brief.lines == [] and brief.actions == []
    assert "the briefing line names 'gossip', which is not one of the tokens it was given" in (
        brief.findings
    )
    assert "the suggested action names 'directive', which is not one of the tokens it was given" in (
        brief.findings
    )


def test_an_action_without_a_kind_is_left_out_because_no_kind_was_named():
    _, report = asked(
        drafting(
            lines=lambda seen: [],
            actions=lambda seen: [follow_up(kind=None, events=seen["events"], evidence=seen["quoted"])],
        )
    )
    brief = one(report)
    assert brief.actions == []
    assert any("names no kind of next step, so it is left out" in line for line in brief.findings)


def test_a_line_or_action_that_came_back_with_no_words_in_it_is_reported():
    _, report = asked(
        drafting(
            lines=lambda seen: [situation(seen["events"], seen["quoted"], body="   ")],
            actions=lambda seen: [follow_up(action="", events=seen["events"])],
        )
    )
    brief = one(report)
    assert "a briefing line came back with no words in it" in brief.findings
    assert "a suggested action came back with no words in it" in brief.findings


def test_a_line_naming_neither_an_event_nor_a_saved_item_is_left_out():
    _, report = asked(drafting(lines=lambda seen: [situation([], [])]))
    brief = one(report)
    assert brief.lines == []
    assert any(
        "the situation line names nothing it follows from, so it is left out" in line
        for line in brief.findings
    )


@pytest.mark.parametrize(
    "text",
    [
        "The municipality completed the clearing work.",
        "Repair of the line was done after the first report.",
        "Action taken on the canal breach is visible here.",
        "The culvert was ordered before this period.",
    ],
)
def test_a_briefing_never_claims_that_something_was_done(text: str):
    assert past_claims_in([text])
    _, report = asked(
        drafting(
            headline=text,
            lines=lambda seen: [situation(seen["events"], seen["quoted"], body=text)],
            caveats=[text],
            actions=lambda seen: [follow_up(action=text, events=seen["events"], evidence=seen["quoted"])],
        )
    )
    brief = one(report)
    assert brief.headline is None
    assert brief.lines == [] and brief.caveats == [] and brief.actions == []
    assert sum("says something was done" in line for line in brief.findings) == 4
    assert brief.state is BriefState.WITHHELD


def test_a_briefing_that_uses_a_certainty_word_is_qualified_never_issued():
    _, report = asked(drafting(headline="This proves the district floods every monsoon"))
    brief = one(report)
    assert "the briefing claims proves" in brief.findings
    assert brief.state is BriefState.QUALIFIED


def test_a_department_no_record_of_those_events_names_is_never_assigned():
    _, report = asked(
        drafting(
            lines=lambda seen: [],
            actions=lambda seen: [
                follow_up(
                    department="power_tangedco",
                    events=seen["events"],
                    evidence=seen["quoted"],
                )
            ],
        )
    )
    brief = one(report)
    assert len(brief.actions) == 1
    assert brief.actions[0].department is Department.UNRESOLVED
    assert any("a department no record of the events it rests on names" in line for line in brief.findings)


def test_a_department_the_events_own_records_name_is_carried_onto_the_action():
    _, report = asked(
        pair(),
        families=grouped_pair(),
        days_back=30,
        body=drafting(
            actions=lambda seen: [
                follow_up(
                    department="water_resources",
                    target_id=seen["saved"][0],
                    target_event=None,
                    events=seen["events"],
                    evidence=seen["quoted"],
                )
            ],
        ),
    )
    brief = one(report)
    drafted = [item for item in brief.actions if item.origin is ActionOrigin.BRIEFING]
    assert len(drafted) == 1
    assert drafted[0].department is Department.WATER_RESOURCES
    assert not any("no record of the events it rests on names" in line for line in brief.findings)
    assert [item.origin for item in brief.actions] == [
        ActionOrigin.SAVED_ISSUE_FAMILY,
        ActionOrigin.BRIEFING,
    ]


def test_only_the_first_lines_and_actions_are_kept():
    _, report = asked(
        drafting(
            lines=lambda seen: [
                situation(seen["events"], seen["quoted"], section="situation")
                for _ in range(MAX_BRIEF_LINES + 2)
            ],
            actions=lambda seen: [
                follow_up(target_event=seen["events"][0], events=seen["events"], evidence=seen["quoted"])
                for _ in range(MAX_BRIEF_ACTIONS + 1)
            ],
        )
    )
    brief = one(report)
    assert (len(brief.lines), len([i for i in brief.actions if i.origin is ActionOrigin.BRIEFING])) == (
        MAX_BRIEF_LINES,
        MAX_BRIEF_ACTIONS,
    )
    assert any("more than 6 lines, so only the first 6 are kept" in line for line in brief.findings)
    assert any("more than 5 actions, so only the first 5 are kept" in line for line in brief.findings)


def test_a_low_confidence_briefing_waits_for_a_person_rather_than_issuing():
    _, report = asked(drafting(confidence=0.4))
    brief = one(report)
    assert brief.confidence == 0.4
    assert brief.review is ReviewState.REVIEW_REQUIRED
    assert brief.state is BriefState.QUALIFIED


def test_a_line_under_the_threshold_is_held_even_when_it_cites_a_quotation():
    _, report = asked(
        drafting(lines=lambda seen: [situation(seen["events"], seen["quoted"], confidence=0.5)])
    )
    brief = one(report)
    assert brief.lines[0].review is ReviewState.REVIEW_REQUIRED
    assert brief.state is BriefState.QUALIFIED
    assert any("a person has not confirmed" in line for line in brief.findings)
    with pytest.raises(ValidationError, match="an accepted line needs confidence of at least"):
        BriefLine(
            section=BriefSection.SITUATION,
            body="A line that accepted itself.",
            item_ids=["EVT-a"],
            evidence_ids=["ev-A"],
            confidence=0.5,
            review=ReviewState.ACCEPTED,
        )


def test_a_briefing_whose_citations_reach_only_one_event_is_not_issued():
    _, report = asked(
        drafting(lines=lambda seen: [situation(seen["events"][:1], seen["quoted"][:1])])
    )
    brief = one(report)
    assert brief.quoted_events == 1 < MIN_QUOTED_EVENTS <= brief.events
    assert any(
        "below the 2 one briefing leans on" in line for line in brief.findings
    )
    assert brief.state is BriefState.QUALIFIED


def test_an_answer_with_nothing_to_say_withholds_the_briefing_and_says_so():
    _, report = asked(drafting(lines=[], actions=[], caveats=["Nothing here carried a quotation."]))
    brief = one(report)
    assert brief.state is BriefState.WITHHELD
    assert brief.lines == [] and brief.actions == []
    assert "nothing was said or suggested that carried its own citation" in brief.findings
    assert brief.headline is not None


def test_a_confidence_that_is_not_a_number_is_said_so_and_defaulted():
    _, report = asked(drafting(confidence=None))
    brief = one(report)
    assert brief.confidence == DEFAULT_CONFIDENCE == 0.5
    assert any(f"gave confidence None; {DEFAULT_CONFIDENCE} was used" in line for line in brief.findings)
    assert brief.state is BriefState.QUALIFIED
    _, held = asked(
        drafting(lines=lambda seen: [situation(seen["events"], seen["quoted"], confidence=None)])
    )
    assert any(
        f"the situation line gave confidence None; {DEFAULT_CONFIDENCE} was used" in line
        for line in one(held).findings
    )
    assert one(held).lines[0].confidence == DEFAULT_CONFIDENCE
    assert one(held).lines[0].review is ReviewState.REVIEW_REQUIRED


def test_a_briefing_asked_in_tamil_and_written_in_tamil_is_issued():
    _, report = asked(
        language=BriefLanguage.TAMIL,
        body=drafting(
            language="tamil",
            headline="ஒரே ஊரில் பிரச்சனை மீண்டும் நடந்தது",
            lines=lambda seen: [situation(seen["events"], seen["quoted"], body=TA_LINE)],
        ),
    )
    brief = one(report)
    assert brief.language is BriefLanguage.TAMIL
    assert brief.state is BriefState.ISSUED
    assert brief.findings == []
    assert "Written in tamil; place names and quotations stay as the records write them." in (
        briefing_document(brief)
    )


def test_writing_in_another_script_than_the_one_asked_is_named_and_held():
    _, report = asked(language=BriefLanguage.TAMIL, body=drafting())
    brief = one(report)
    assert brief.state is BriefState.QUALIFIED
    assert "the briefing answered in english although tamil was asked for it" in brief.findings
    assert any(
        f"this was asked for in {BriefLanguage.TAMIL.value} and wrote" in line
        and "in another script" in line
        for line in brief.findings
    )
    assert script_mismatch([brief.headline], BriefLanguage.ENGLISH) == []
    assert script_mismatch([brief.headline], BriefLanguage.TAMIL) == [brief.headline]


def test_a_tamil_line_answered_when_english_was_asked_is_flagged():
    _, report = asked(
        drafting(lines=lambda seen: [situation(seen["events"], seen["quoted"], body=TA_LINE)])
    )
    brief = one(report)
    assert any("in another script" in line for line in brief.findings)
    assert brief.state is BriefState.QUALIFIED


def test_an_answer_that_is_not_a_briefing_is_a_failure_not_a_quiet_empty_one():
    judge, report = asked(body=lambda request: {"lines": "nothing at all"})
    brief = one(report)
    assert report.failed and report.asked == 0
    assert "is not shaped like one" in report.failed[0]
    assert brief.state is BriefState.WITHHELD
    assert brief.provider == NOT_ASKED
    assert len(judge.asked) == 1


def test_a_provider_that_never_answers_leaves_a_withheld_briefing_and_a_stop_reason():
    class Refusing(LLMProvider):
        @property
        def model(self) -> str:
            return "refusing"

        def complete(self, request: LLMRequest) -> Any:
            raise LLMError("the endpoint gave up")

    report = district_briefing(
        three(),
        chain_factory=lambda: Chain.single(Refusing()),
        now=NOW,
        days_back=DAYS,
    )
    assert report.failed
    assert "no usable briefing came back" in report.failed[0]
    assert one(report).state is BriefState.WITHHELD
    assert one(report).lines == [] and one(report).actions == []


def test_an_exhausted_quota_stops_drafting_and_says_what_was_left():
    def refused(request: LLMRequest) -> dict[str, Any]:
        raise QuotaExhaustedError("daily tokens used up")

    judge, report = asked(refused)
    assert report.asked == 0
    assert report.stop_reason is not None
    assert one(report).state is BriefState.WITHHELD
    assert any("the chain stopped drafting" in line for line in report.limitations())


def test_a_saved_briefing_is_reused_while_the_period_and_the_prompt_match(tmp_path):
    store = BriefStore(tmp_path)
    asked(three(), days_back=DAYS)
    judge, first = asked(three(), days_back=DAYS)
    district_briefing(
        three(),
        chain_factory=lambda: Chain.single(judge),
        now=NOW,
        days_back=DAYS,
        store=store.load(),
    )
    reuse = Answering(drafting())
    second = district_briefing(
        three(),
        chain_factory=lambda: Chain.single(reuse),
        now=NOW,
        days_back=DAYS,
        store=BriefStore(tmp_path).load(),
    )
    assert second.reused == 1 and second.asked == 0
    assert reuse.asked == []
    assert one(second).briefing_id == one(first).briefing_id


def test_a_period_that_changed_is_asked_again_rather_than_reused(tmp_path):
    judge, _ = asked(three(), days_back=DAYS)
    store = BriefStore(tmp_path)
    district_briefing(
        three(), chain_factory=lambda: Chain.single(judge), now=NOW, days_back=DAYS, store=store.load()
    )
    again = Answering(drafting())
    report = district_briefing(
        three(),
        chain_factory=lambda: Chain.single(again),
        now=NOW,
        days_back=14,
        store=BriefStore(tmp_path).load(),
    )
    assert report.reused == 0 and report.asked == 1
    assert len(again.asked) == 1


def test_the_summary_file_holds_the_briefing_and_the_document_a_component_would_read(tmp_path):
    judge, report = asked(three(), days_back=DAYS)
    BriefStore(tmp_path).save(report)
    summary = json.loads((tmp_path / BRIEF_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert summary["prompt_version"] == "brief-1"
    assert summary["prompt_digest"] == BRIEF_PROMPT_DIGEST
    assert summary["period"] == "2026-07-21..2026-09-28"
    assert summary["counts"]["Distinct events"] == 3
    assert summary["limitations"]
    entry = summary["briefings"][0]
    assert entry["briefing"]["briefing_id"].startswith("BRF-")
    assert "District briefing BRF-" in entry["document"]
    lines = (tmp_path / BRIEFINGS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert Briefing.model_validate_json(lines[0]).briefing_id == entry["briefing"]["briefing_id"]


def test_a_damaged_briefings_file_stops_the_rather_than_being_overwritten(tmp_path):
    (tmp_path / BRIEFINGS_FILENAME).write_text("{ not json\n", encoding="utf-8")
    with pytest.raises(CheckpointError, match="is not a briefing this pipeline wrote"):
        BriefStore(tmp_path).load()
    judge = Answering(drafting())
    with pytest.raises(CheckpointError):
        district_briefing(
            three(),
            chain_factory=lambda: Chain.single(judge),
            now=NOW,
            days_back=DAYS,
            store=BriefStore(tmp_path).load(),
        )
    assert (tmp_path / BRIEFINGS_FILENAME).read_text(encoding="utf-8") == "{ not json\n"


def test_a_hand_edited_briefing_cannot_arrive_as_issued(tmp_path):
    judge, report = asked(three(), days_back=DAYS)
    brief = one(report)
    payload = brief.model_dump(mode="json")
    payload["lines"][0]["body"] = "The repair was done after the last report."
    with pytest.raises(ValidationError, match="says something was done"):
        Briefing.model_validate(payload)
    qualified = one(asked(drafting(confidence=0.4))[1])
    tampered = qualified.model_dump(mode="json")
    tampered["state"] = BriefState.ISSUED.value
    tampered["review"] = ReviewState.ACCEPTED.value
    with pytest.raises(ValidationError, match="carries a finding"):
        Briefing.model_validate(tampered)


def test_a_line_reaching_for_an_event_outside_the_period_is_refused_by_the_stored_model():
    brief = one(asked()[1])
    payload = brief.model_dump(mode="json")
    payload["lines"][0]["item_ids"] = ["EVT-nothing-prints-this"]
    with pytest.raises(ValidationError, match="which was not printed with it"):
        Briefing.model_validate(payload)
    payload = brief.model_dump(mode="json")
    payload["lines"][0]["evidence_ids"] = ["ev-never-printed"]
    with pytest.raises(ValidationError, match="carries no quotation for"):
        Briefing.model_validate(payload)


def test_a_stored_item_carrying_an_event_of_no_period_is_refused():
    brief = one(asked(pair(), families=grouped_pair(), days_back=30)[1])
    assert brief.items and brief.citations
    payload = brief.model_dump(mode="json")
    payload["items"][0]["events"] = len(payload["items"][0]["event_ids"]) + 1
    payload["items"][0]["event_ids"] = payload["items"][0]["event_ids"] + ["EVT-outside-this-period"]
    with pytest.raises(ValidationError, match="lists an event outside the period"):
        Briefing.model_validate(payload)
    payload = brief.model_dump(mode="json")
    payload["citations"][0]["event_id"] = "EVT-outside-this-period"
    with pytest.raises(ValidationError, match="did not count in its period"):
        Briefing.model_validate(payload)


def test_the_console_prints_the_briefing_and_the_limits_a_reader_needs():
    _, report = asked(
        drafting(
            actions=lambda seen: [
                follow_up(target_event=seen["events"][0], events=seen["events"], evidence=seen["quoted"])
            ]
        )
    )
    out = io.StringIO()
    print_briefings(report, stream=out)
    printed = out.getvalue()
    assert f"District briefing (brief-1, {BRIEF_PROMPT_DIGEST}, incident contract 2.0)" in printed
    assert "  Period: 2026-07-21..2026-09-28" in printed
    assert "  Distinct events: 3" in printed
    assert "  Suggested actions: 1" in printed
    assert "     headline: One problem kept returning in one village" in printed
    assert "     [situation] This keeps happening in the same village." in printed
    assert "suggested (follow_up, briefing): Check the supply line before the next failure." in printed
    assert "a person's decision, never recorded as done" in printed
    assert "  limitation: a suggested action is what a person could do next" in printed
    assert "  limitation: this briefing covers only the records this run read" in printed
    assert "[ev-NEWS-" not in printed
    detailed = io.StringIO()
    print_briefings(report, detailed=True, stream=detailed)
    deep = detailed.getvalue()
    assert len(deep.splitlines()) > len(printed.splitlines())
    assert "Suggested actions - each is a person's decision, never recorded as done here:" in deep


def test_the_console_says_a_withheld_briefing_was_withheld():
    judge, report = asked(drafting(lines=[], actions=[]))
    out = io.StringIO()
    print_briefings(report, stream=out)
    printed = out.getvalue()
    assert "withheld (confidence" in printed
    assert "  failed" not in printed
    assert "Briefing drafted: 1" in printed


def cli_reading() -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        version = request.meta.get("prompt_version")
        if version == BRIEF_PROMPT_VERSION:
            seen = seen_of(request)
            return {
                "headline": "Waterlogging kept being reported in Madurai",
                "language": "english",
                "lines": [situation(seen["events"], seen["quoted"])],
                "actions": [
                    follow_up(
                        target_event=seen["events"][-1],
                        events=seen["events"],
                        evidence=seen["quoted"],
                    )
                ],
                "caveats": [],
                "saved": [],
                "confidence": 0.9,
            }
        body = water_answer()
        body.pop("event_time")
        return body

    return answer


def cli_records() -> list[dict[str, Any]]:
    days = ("2026-07-13", "2026-08-20", RECENT[0], RECENT[1], RECENT[2])
    return [
        waterlogging(
            record_id=f"NEWS-MDU-WLR-{index:04d}",
            event_time=f"{day}T08:30:00",
            source_url=f"https://www.dinamalar.com/news/madurai/rain/{index}",
            raw_reference=f"https://www.dinamalar.com/news/madurai/rain/{index}",
        )
        for index, day in enumerate(days)
    ]


def test_the_console_writes_one_district_briefing_over_the_records_it_read(tmp_path, capsys):
    records = cli_records()
    judge = Answering(cli_reading())
    argv = ["--all", "--briefing", "--brief-days", "120", "--state-dir", str(tmp_path)]
    code = main(argv, provider=judge, environ={}, transport=responder(listing(*records)))
    printed = capsys.readouterr().out
    assert code == EXIT_OK, printed
    assert f"District briefing (brief-1, {BRIEF_PROMPT_DIGEST}" in printed
    assert "  Distinct events: 5" in printed
    assert "  Briefing drafted: 1" in printed
    assert "  suggested (follow_up, briefing):" in printed
    assert (tmp_path / BRIEFINGS_FILENAME).exists() and (tmp_path / BRIEF_SUMMARY_FILENAME).exists()

    reuse = Answering(cli_reading())
    code = main(argv, provider=reuse, environ={}, transport=responder(listing(*records)))
    printed = capsys.readouterr().out
    assert code == EXIT_OK, printed
    assert "  Briefing reused: 1" in printed
    assert [
        request.meta.get("prompt_version") for request in reuse.asked
    ] != [BRIEF_PROMPT_VERSION]


def test_the_console_refuses_a_brief_request_that_would_set_the_same_period_twice(capsys):
    for argv in (
        ["--briefing"],
        ["--all", "--briefing", "--brief-days", "30", "--since", "2026-09-01"],
        ["--all", "--briefing", "--brief-days", "0"],
        ["--all", "--briefing", "--brief-days", str(MAX_BRIEF_DAYS + 1)],
        ["--all", "--brief-days", "30"],
        ["--all", "--brief-language", "tamil"],
        ["--all", "--briefing", "--brief-language", "sign"],
    ):
        with pytest.raises(SystemExit):
            main(argv, environ={})
        assert "usage:" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--briefing"], environ={})
    assert "say which to read" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--all", "--brief-days", "30"], environ={})
    assert "--briefing for them to mean something" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--all", "--briefing", "--brief-days", "30", "--since", "2026-09-01"], environ={})
    assert "give one of them" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(
            ["--all", "--briefing", "--since", "2026-09-01", "--until", "2026-01-01"],
            environ={},
        )
    assert "--until must be on or after --since" in capsys.readouterr().err


def test_the_console_counts_filters_as_a_briefing_input(capsys):
    with pytest.raises(SystemExit):
        main(["--all", "--place", "Madurai"], environ={})
    assert "--recurring-issues, --emerging-trends or --briefing" in capsys.readouterr().err


def test_the_console_defers_when_the_briefing_provider_has_no_allowance_left(tmp_path, capsys):
    records = cli_records()

    def refused(request: LLMRequest) -> dict[str, Any]:
        if request.meta.get("prompt_version") == BRIEF_PROMPT_VERSION:
            raise QuotaExhaustedError("daily tokens used up")
        body = water_answer()
        body.pop("event_time")
        return body

    code = main(
        ["--all", "--briefing", "--brief-days", "120", "--state-dir", str(tmp_path)],
        provider=Answering(refused),
        environ={},
        transport=responder(listing(*records)),
    )
    printed = capsys.readouterr().out
    assert code == EXIT_DEFERRED, printed
    assert "  Stopped:" in printed


def test_the_console_exits_partial_when_the_briefing_answer_is_unusable(tmp_path, capsys):
    records = cli_records()

    def nonsense(request: LLMRequest) -> dict[str, Any]:
        if request.meta.get("prompt_version") == BRIEF_PROMPT_VERSION:
            return {"lines": "nothing a briefing can be built from"}
        body = water_answer()
        body.pop("event_time")
        return body

    code = main(
        ["--all", "--briefing", "--brief-days", "120", "--state-dir", str(tmp_path)],
        provider=Answering(nonsense),
        environ={},
        transport=responder(listing(*records)),
    )
    printed = capsys.readouterr().out
    assert code == EXIT_PARTIAL, printed
    assert "  failed BRF-" in printed


def test_the_console_stops_on_a_damaged_briefing_file(tmp_path, capsys):
    (tmp_path / BRIEFINGS_FILENAME).write_text("{ not json\n", encoding="utf-8")
    code = main(
        ["--all", "--briefing", "--state-dir", str(tmp_path)],
        provider=Answering(cli_reading()),
        environ={},
        transport=responder(listing(*cli_records())),
    )
    assert code == EXIT_CHECKPOINT
    assert "checkpoint" in capsys.readouterr().out.lower() or capsys.readouterr().err


def test_a_briefing_prints_a_measured_increase_that_an_action_can_follow():
    incidents, links, trends = trended()
    _, issues = grouped(incidents, grouping())
    assert [f.review for f in issues.families] == [ReviewState.REVIEW_REQUIRED]
    report = asked(
        incidents,
        links=links,
        families=issues.families,
        measured=trends,
        days_back=95,
        body=drafting(
            saved=lambda seen: seen["saved"][:1],
            lines=lambda seen: [
                situation(seen["events"], seen["quoted"], section="rising", saved=seen["saved"][:1])
            ],
            actions=lambda seen: [
                follow_up(
                    target_id=seen["saved"][0],
                    target_event=None,
                    section="rising",
                    events=seen["events"],
                    evidence=seen["quoted"],
                )
            ],
        ),
    )[1]
    brief = one(report)
    assert {item.kind for item in brief.items} == {BriefItemKind.MEASURED_INCREASE}
    assert all(item.alert_eligible is True for item in brief.items)
    assert set(brief.lines[0].item_ids) & set(brief.item_ids)
    assert brief.named_items and set(brief.named_items) <= set(brief.item_ids)
    drafted = [item for item in brief.actions if item.origin is ActionOrigin.BRIEFING]
    assert len(drafted) == 1
    assert drafted[0].target_kind == BriefItemKind.MEASURED_INCREASE.value
    assert drafted[0].target_id in brief.item_ids
    assert brief.state is BriefState.ISSUED


def test_a_saved_item_named_by_a_line_that_was_never_printed_is_named_as_one():
    _, report = asked(
        drafting(lines=lambda seen: [situation([], [], saved=["ISS-0000000000000000"], section="situation")])
    )
    brief = one(report)
    assert any(
        "which is neither a saved recurring issue nor a measured increase printed with it" in line
        for line in brief.findings
    )


def test_measured_and_drafting_never_reach_the_same_item_twice_under_one_name():
    families = grouped_pair()
    refs = item_refs(families, [], measure(pair(), [], Filters(), 30))
    assert len(refs) == len({(item.kind, item.item_id) for item in refs}) == 1
    brief = one(asked(pair(), families=families, days_back=30)[1])
    assert [item.item_id for item in brief.items] == [refs[0].item_id]
    payload = brief.model_dump(mode="json")
    payload["items"] = payload["items"] * 2
    payload["briefing_id"] = brief_id_for(
        brief.since,
        brief.until,
        brief.days_back,
        brief.language,
        brief.event_ids,
        [item["item_id"] for item in payload["items"]],
    )
    with pytest.raises(ValidationError, match="one reference per saved item"):
        Briefing.model_validate(payload)


def test_the_counts_a_briefing_prints_are_the_counts_the_pipeline_made():
    _, report = asked()
    brief = one(report)
    counts = report.counts()
    assert counts["Events with no date"] == brief.undated
    assert counts["Events outside the period"] == brief.outside_period
    assert counts["Open with a quote"] == len(brief.open_followups)
    assert counts["Action or resolution recorded"] == len(brief.recorded_responses)
    assert counts["Document records"] == len(brief.document_records)
    assert counts["Suggested actions"] == len(brief.actions)
    assert isinstance(brief.filters, Filters)
    assert brief.schema_version == "2.0"
    assert brief.prompt_version == BRIEF_PROMPT_VERSION and brief.prompt_digest == BRIEF_PROMPT_DIGEST


def test_an_issued_briefing_that_stops_citing_is_refused_by_the_stored_model():
    brief = one(asked()[1])
    assert brief.state is BriefState.ISSUED
    payload = brief.model_dump(mode="json")
    payload["citations"] = []
    with pytest.raises(ValidationError, match="carries no quotation for"):
        Briefing.model_validate(payload)
