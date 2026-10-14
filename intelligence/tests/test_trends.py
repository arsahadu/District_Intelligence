from __future__ import annotations

import io
import json
import re
from datetime import date, timedelta
from typing import Any, Callable, Optional, Sequence

import pytest
from pydantic import ValidationError

import intelligence.trends as trends_module
from intelligence.checkpoint import CheckpointError
from intelligence.contract import EventType, Incident, ReviewState
from intelligence.fallback import Chain
from intelligence.llm import LLMError, LLMRequest, QuotaExhaustedError
from intelligence.linking import EventLink, LinkReport, eligible, link_incidents
from intelligence.pipeline import (
    EXIT_CHECKPOINT,
    EXIT_DEFERRED,
    EXIT_OK,
    EXIT_PARTIAL,
    main,
)
from intelligence.recurrence import Filters, event_units, recurring_issues
from intelligence.trends import (
    BASELINE_BLOCKS,
    CLAIM_WORDS,
    COVERAGE_DRIFT,
    MAX_TREND_EVENTS,
    MAX_TREND_REQUESTS,
    MIN_BASELINE_BLOCKS,
    MIN_TREND_EVENTS,
    MIN_TREND_RATIO,
    NOT_ASKED,
    RECENT_DAYS,
    TREND_PROMPT_DIGEST,
    TREND_PROMPT_VERSION,
    TREND_RULES,
    TREND_SCHEMA,
    TREND_SUMMARY_FILENAME,
    TREND_THRESHOLD,
    TRENDS_FILENAME,
    Candidate,
    Trend,
    TrendArithmetic,
    TrendBlock,
    TrendDimension,
    TrendStore,
    TrendStatus,
    claim_words_in,
    candidate_of,
    coverage_of,
    dimension_groups,
    emerging_trends,
    evidence_rules,
    print_trends,
    spans_for,
    trend_id_for,
    trend_request,
    windows_of,
)
from intelligence.tests.builders import listing, responder, water_answer
from intelligence.tests.record_fixtures import waterlogging
from intelligence.tests.test_linking import NOW, Answering, never, shown_ids
from intelligence.tests.test_recurrence import CLEANED, grouping, note, undated

CRASH_TA = "திருமங்கலம் அருகே இரு சக்கர வாகனம் கவிழ்ந்ததில் ஒருவர் காயமடைந்தார்"
PUBLISHERS = ("dinamalar", "the_hindu", "dinakaran")
RECENT = ("2026-09-15", "2026-09-25", "2026-10-10")
MONTH_TA = {7: "ஜூலை", 8: "ஆகஸ்ட்", 9: "செப்டம்பர்", 10: "அக்டோபர்"}


def spoken(day: str) -> str:
    return f"{MONTH_TA[int(day[5:7])]} {int(day[8:10])} அன்று"


def flooding(
    tag: str,
    day: str,
    index: int = 0,
    *,
    source: Optional[str] = None,
    pending: bool = False,
    **fields: Any,
) -> Incident:
    content = (
        f"சோழவந்தான் கிராமத்தில் {spoken(day)} வைகை ஆற்று வெள்ளம் 40 வீடுகளை மூழ்கடித்தது. "
        f"{CLEANED}"
    )
    areas: dict[str, Any] = {"village": "Sholavandan"}
    if pending:
        areas["taluk"] = "Vadipatti"
    return note(
        f"NEWS-{tag}{index}",
        content,
        kind="flood",
        kind_quote="வைகை ஆற்று வெள்ளம்",
        place="Sholavandan",
        place_quote="சோழவந்தான் கிராமத்தில்",
        location_fields=areas,
        when=f"{day}T00:00:00",
        date_quote=spoken(day),
        source_id=source or PUBLISHERS[index],
        published=f"{day}T09:00:00",
        **fields,
    )


def accident(tag: str, day: str, *, source: str = "dinamalar") -> Incident:
    return note(
        f"NEWS-{tag}0",
        CRASH_TA,
        kind="vehicle_accident",
        kind_quote="இரு சக்கர வாகனம் கவிழ்ந்ததில்",
        place="Thirumangalam",
        place_quote="திருமங்கலம் அருகே",
        location_type="taluk",
        location_fields={"taluk": "Thirumangalam"},
        when=f"{day}T00:00:00",
        source_id=source,
        published=f"{day}T09:00:00",
    )


def trio(tag: str, day: str, **fields: Any) -> list[Incident]:
    return [flooding(tag, day, index, **fields) for index in range(3)]


def pair(tag: str, day: str, **fields: Any) -> list[Incident]:
    return [flooding(tag, day, index, **fields) for index in range(2)]


def one(tag: str, day: str, **fields: Any) -> list[Incident]:
    return [flooding(tag, day, 0, **fields)]


def steady() -> list[Incident]:
    return [
        *trio("J", "2026-07-13"),
        *trio("A", "2026-08-20"),
        *one("R1", RECENT[0]),
        *one("R2", RECENT[1]),
        *one("R3", RECENT[2]),
    ]


def mild() -> list[Incident]:
    return [
        *pair("J", "2026-07-13"),
        *one("J2", "2026-07-20"),
        *pair("A", "2026-08-15"),
        *one("A2", "2026-08-25"),
        *one("R1", RECENT[0]),
        *one("R2", RECENT[1]),
        *one("R3", RECENT[2]),
    ]


def burst() -> list[Incident]:
    unread = [flooding(f"P{day}", f"2026-09-{day}", pending=True) for day in
              ("12", "16", "18", "22", "27", "30")]
    return [*steady(), *unread]


def thicker() -> list[Incident]:
    recent = []
    for index, day in enumerate(RECENT):
        recent += [
            flooding(f"K{index}", day, 0),
            flooding(f"K{index}", day, 1),
            flooding(f"K{index}c", day, 0, source="dinamalar"),
        ]
    return [flooding("J", "2026-07-13", 0), flooding("A", "2026-08-20", 1), *recent]


def gap() -> list[Incident]:
    return [
        *trio("J", "2026-07-13"),
        *one("R1", RECENT[0]),
        *one("R2", RECENT[1]),
        *one("R3", RECENT[2]),
    ]


def appeared() -> list[Incident]:
    return [
        *pair("R1", RECENT[0]),
        *one("R2", RECENT[1]),
        *one("R3", RECENT[2]),
        accident("C1", "2026-07-20", source="dinamalar"),
        accident("C2", "2026-08-20", source="the_hindu"),
    ]


def judging(away: float = 0.92) -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        days = [
            re.search(r"^event_time: (\S+) \(", side, re.M)
            for side in request.user.split("\n\nReport B\n")
        ]
        same = all(days) and days[0].group(1)[:10] == days[1].group(1)[:10]
        return {
            "verdict": "same_event" if same else "separate_event",
            "confidence": 0.92 if same else away,
            "evidence_a": shown_ids(request, "A")[:2] if same else [],
            "evidence_b": shown_ids(request, "B")[:2] if same else [],
            "conflicts": [],
            "explanation": "One happening" if same else "Two happenings",
        }

    return answer


def merged(incidents: Sequence[Incident], *, away: float = 0.92) -> LinkReport:
    judge = Answering(judging(away))
    found = link_incidents(incidents, chain_factory=lambda: Chain.single(judge), now=NOW)
    assert found.failed == []
    return found


def reading(**changes: Any) -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        body = {
            "title": "வெள்ளம் மீண்டும் வந்தது",
            "pattern": "Three happenings in the recent window against one in each window before it.",
            "one_problem": True,
            "explanations": [
                "The monsoon may have returned.",
                "More reporters may be covering the village.",
            ],
            "cited_events": [1, 2],
            "confidence": 0.9,
        }
        return body | changes

    return answer


def measured(
    incidents: Sequence[Incident],
    links: Sequence[EventLink] = (),
    answer: Optional[Callable[[LLMRequest], dict[str, Any]]] = None,
    **fields: Any,
) -> tuple[Answering, Any]:
    judge = Answering(answer or reading())
    return judge, emerging_trends(
        incidents,
        chain_factory=lambda: Chain.single(judge),
        links=links,
        now=NOW,
        **fields,
    )


def candidate(
    incidents: Sequence[Incident],
    links: Sequence[EventLink],
    dimension: TrendDimension,
) -> Candidate:
    units = [unit for unit in event_units(eligible(incidents), links) if unit.day is not None]
    spans, blocked = spans_for(
        min(unit.day for unit in units),
        max(unit.day for unit in units),
        RECENT_DAYS,
        BASELINE_BLOCKS,
    )
    assert blocked is None
    found = candidate_of(
        [group for group in dimension_groups(units, []) if group.dimension is dimension][0],
        spans,
        coverage_of(incidents, spans),
        recent_days=RECENT_DAYS,
        min_events=MIN_TREND_EVENTS,
        min_ratio=MIN_TREND_RATIO,
        coverage_drift=COVERAGE_DRIFT,
    )
    assert found is not None
    return found


def of(report: Any, dimension: TrendDimension) -> Trend:
    return [item for item in report.trends if item.dimension is dimension][0]


def test_the_trend_prompt_is_pinned_and_the_model_is_never_asked_for_a_number():
    assert TREND_PROMPT_VERSION == "trend-1"
    assert TREND_PROMPT_DIGEST == "dcd6650c67435249"
    for phrase in (
        "Every number printed was measured from the records",
        "no answer of yours can raise or lower what the measurements decided",
        "Answer false when they are several",
        "Never add a place, a number, a date or a toll the shown events do not carry",
        "a coverage change and a real change look identical in these numbers",
        "Never invent an index and never name an event you were not shown",
        "when you are guessing, answer below 0.5",
    ):
        assert phrase in TREND_RULES
    assert sorted(TREND_SCHEMA["properties"]) == [
        "cited_events", "confidence", "explanations", "one_problem", "pattern", "title"]
    document = json.dumps(TREND_SCHEMA)
    for absent in ("events", "incidents", "records", "count", "ratio", "window"):
        assert absent not in document.replace("cited_events", "")


def test_the_windows_are_equal_and_back_to_back_or_no_increase_is_measured():
    spans, blocked = spans_for(date(2026, 7, 13), date(2026, 10, 10), 30, 3)
    assert blocked is None
    assert spans == [
        (date(2026, 7, 13), date(2026, 8, 11)),
        (date(2026, 8, 12), date(2026, 9, 10)),
        (date(2026, 9, 11), date(2026, 10, 10)),
    ]
    assert all(high - low == timedelta(days=29) for low, high in spans)
    assert spans[0][1] + timedelta(days=1) == spans[1][0]

    thin, refusal = spans_for(date(2026, 9, 20), date(2026, 10, 10), 30, 3)
    assert thin == []
    assert "leaves 0 complete window(s) of 30 day(s)" in refusal
    assert "so no increase was measured" in refusal

    empty = windows_of([], Filters(), RECENT_DAYS, BASELINE_BLOCKS)
    assert empty.blocked == "no event the run counted carries a date, so nothing can sit in a window"

    incidents = steady()
    counted = event_units(eligible(incidents), merged(incidents).links)
    reversed_ = windows_of(
        counted, Filters(since=date(2026, 10, 5), until=date(2026, 10, 1)), RECENT_DAYS, BASELINE_BLOCKS
    )
    assert "ends 2026-10-01, before it starts on 2026-10-05" in reversed_.blocked
    assert reversed_.spans == []
    assert MIN_BASELINE_BLOCKS == 2


def test_a_rise_in_distinct_events_is_alert_eligible_only_when_every_requirement_is_met():
    incidents = steady()
    links = merged(incidents)
    judge, report = measured(incidents, links.links)

    assert (report.candidates, report.asked, report.reused) == (2, 2, 0)
    assert len(judge.asked) == 2
    assert [item.status for item in report.trends] == [
        TrendStatus.ALERT_ELIGIBLE, TrendStatus.ALERT_ELIGIBLE]
    assert all(item.findings == [] and item.review is ReviewState.ACCEPTED for item in report.trends)

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert (trend.events, trend.incidents, trend.records) == (3, 3, 3)
    assert [(block.since, block.events, block.incidents, block.records, block.pool_records)
            for block in trend.arithmetic.blocks] == [
        (date(2026, 7, 13), 1, 3, 3, 3),
        (date(2026, 8, 12), 1, 3, 3, 3),
        (date(2026, 9, 11), 3, 3, 3, 3),
    ]
    assert (trend.arithmetic.baseline_mean, trend.arithmetic.ratio) == (1.0, 3.0)
    assert trend.arithmetic.share_ratio == 3.0
    assert (trend.first_event, trend.last_event) == (date(2026, 9, 15), date(2026, 10, 10))
    assert trend.trend_id == trend_id_for(
        TrendDimension.INCIDENT_TYPE, "flood", date(2026, 9, 11), date(2026, 10, 10),
        trend.event_ids)
    assert sorted(ref.record_id for ref in trend.references) == ["NEWS-R10", "NEWS-R20", "NEWS-R30"]
    assert all(ref.evidence_ids and len(ref.quotes) == len(ref.evidence_ids)
               for ref in trend.references)
    assert [(area.level, area.name) for area in trend.areas] == [("village", "Sholavandan")]
    assert trend.geographic_precision == "village"
    assert trend.incident_types == ["flood"] and trend.categories == []
    assert trend.source_urls == sorted(
        f"https://news.example.test/{key}" for key in ("NEWS-R10", "NEWS-R20", "NEWS-R30"))
    assert (trend.deduplicated, trend.links, trend.undecided_links) == (True, 6, 0)
    assert (trend.provider, trend.model, trend.decided_at) == ("scripted", "gpt-oss-120b", NOW)
    assert (trend.reading.confidence, trend.reading.one_problem) == (0.9, True)
    assert len(trend.reading.cited_event_ids) == 2
    assert trend.method is not None and trend.prompt_digest == TREND_PROMPT_DIGEST
    assert report.counts()["Alert-eligible trends"] == 2
    assert report.counts()["Distinct events dated"] == 5
    assert report.counts()["Windows"].startswith("recent 2026-09-11..2026-10-10 (30 days)")
    assert not any("no link decisions" in line for line in report.limitations())


def test_the_count_is_distinct_events_and_not_the_number_of_articles():
    incidents = steady()
    _, with_links = measured(incidents, merged(incidents).links)
    _, without = measured(incidents)

    counted = of(with_links, TrendDimension.INCIDENT_TYPE).arithmetic
    assert (counted.blocks[0].events, counted.blocks[0].records) == (1, 3)
    assert counted.per_event_baseline == 3.0 and counted.per_event_recent == 1.0

    assert (without.deduplicated, without.candidates, without.trends, without.asked) == (
        False, 0, [], 0)
    assert any(
        "repeated reports of one happening count separately in every window" in line
        for line in without.limitations()
    )


def test_a_burst_of_coverage_is_corrected_for_how_many_records_the_run_read():
    incidents = burst()
    _, report = measured(incidents, merged(incidents).links)

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert (trend.status, trend.review) == (TrendStatus.POTENTIAL, ReviewState.REVIEW_REQUIRED)
    assert trend.arithmetic.recent.pool_records == 9
    assert trend.arithmetic.share_recent == 0.3333
    assert trend.arithmetic.share_ratio == 1.0
    assert len(trend.findings) == 1
    assert "what grew may be how much the run read rather than what happened" in trend.findings[0]
    assert report.skipped_review == 6
    assert any("waiting for a person were left out of every count" in line
               for line in report.limitations())


def test_more_reports_per_event_than_the_baseline_is_read_as_more_reporting():
    incidents = thicker()
    links = merged(incidents).links
    _, report = measured(incidents, links)
    trend = of(report, TrendDimension.INCIDENT_TYPE)

    assert (trend.events, trend.incidents, trend.records) == (3, 9, 9)
    assert trend.arithmetic.ratio == 3.0
    assert trend.arithmetic.per_event_recent == 3.0
    assert trend.arithmetic.per_event_baseline == 1.0
    assert trend.status is TrendStatus.POTENTIAL
    assert any("3.00 reports per event against 1.00 in its baseline" in item
               for item in trend.findings)
    assert not any("report in the recent window and not in its baseline" in item
                   for item in trend.findings)

    _, raised = measured(incidents, links, coverage_drift=4.0)
    held = of(raised, TrendDimension.INCIDENT_TYPE)
    assert held.arithmetic.coverage_drift == 4.0
    assert not any("reports per event against" in item for item in held.findings)
    assert any("what grew may be how much the run read" in item for item in held.findings)


def test_a_publisher_the_baseline_never_had_is_not_read_as_more_happening():
    incidents = [
        *trio("J", "2026-07-13"),
        *trio("A", "2026-08-20"),
        *one("R1", RECENT[0]),
        *one("R2", RECENT[1]),
        *one("R3", RECENT[2], source="maalai"),
    ]
    _, report = measured(incidents, merged(incidents).links)

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert trend.status is TrendStatus.POTENTIAL
    assert any("publishers maalai report in the recent window and not in its baseline" in item
               for item in trend.findings)


def test_a_window_the_run_read_no_records_for_is_never_a_quiet_zero():
    incidents = gap()
    _, report = measured(incidents, merged(incidents).links)

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert trend.arithmetic.blocks[1].pool_records == 0
    assert trend.arithmetic.ratio == 6.0
    assert trend.status is TrendStatus.POTENTIAL
    assert trend.findings == [
        "no record of any kind was read for 2026-08-12..2026-09-10, so nothing was counted in that "
        "window rather than nothing happening"
    ]


def test_events_with_no_baseline_are_a_new_appearance_and_never_a_measured_increase():
    incidents = appeared()
    judge, report = measured(
        incidents,
        merged(incidents).links,
        filters=Filters(since=date(2026, 7, 1), incident_type=EventType.FLOOD),
    )

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert (report.candidates, report.asked) == (2, 2)
    assert trend.arithmetic.baseline_mean == 0.0
    assert trend.arithmetic.ratio is None
    assert [block.pool_records for block in trend.arithmetic.baseline] == [1, 1]
    assert trend.status is TrendStatus.POTENTIAL
    assert trend.findings == [
        "the baseline windows hold none of these events, so 3 event(s) now is a new appearance or "
        "newly covered reporting rather than a measured increase"
    ]
    assert "not computed because the baseline holds none of these events" in judge.asked[0].user
    assert "the baseline windows hold none of these events, so there is nothing to correct against" in (
        judge.asked[0].user
    )
    assert report.excluded_by_filter == 2


def test_a_rise_below_the_threshold_stays_potential_and_no_answer_can_raise_it():
    incidents = mild()
    judge, report = measured(
        incidents,
        merged(incidents).links,
        reading(confidence=1.0, cited_events=[1, 2, 3], one_problem=True,
                title="Floods in one village", pattern="More floods than before."),
    )

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert (trend.arithmetic.ratio, trend.arithmetic.share_ratio) == (1.5, 1.5)
    assert trend.status is TrendStatus.POTENTIAL
    assert trend.reading.confidence == 1.0
    assert any("the recent window is 1.50 times its baseline average, under the 2.00 threshold"
               in item for item in trend.findings)
    assert any("a corrected rise of 1.50 times under the 2.00" in item for item in trend.findings)
    assert report.alerts == []
    assert report.counts()["Potential trends"] == 2


def test_the_thresholds_are_settings_the_operator_can_move():
    incidents = mild()
    links = merged(incidents).links
    _, strict = measured(incidents, links)
    _, looser = measured(incidents, links, min_ratio=1.4)
    _, short = measured(incidents, links, recent_days=45)

    assert [item.status for item in strict.trends] == [TrendStatus.POTENTIAL] * 2
    assert all(item.status is TrendStatus.ALERT_ELIGIBLE for item in looser.trends)
    trend = of(looser, TrendDimension.INCIDENT_TYPE)
    assert (trend.arithmetic.min_ratio, trend.arithmetic.min_events) == (1.4, 3)
    assert (trend.arithmetic.coverage_drift, trend.arithmetic.recent_days) == (1.5, 30)
    assert COVERAGE_DRIFT == 1.5 and MIN_TREND_RATIO == 2.0 and MIN_TREND_EVENTS == 3
    assert RECENT_DAYS == 30 and BASELINE_BLOCKS == 3

    assert short.candidates == 0
    assert short.windows.blocked and "leaves 1 complete window(s) of 45 day(s)" in short.windows.blocked
    assert "no increase was measured" in "; ".join(short.limitations())


def test_a_pattern_below_the_minimum_event_count_is_never_a_candidate_or_a_request():
    incidents = steady()
    links = merged(incidents).links

    judge, tight = measured(incidents, links, min_events=MAX_TREND_EVENTS)
    assert (tight.candidates, tight.groups, tight.trends, tight.asked) == (0, 2, [], 0)
    assert judge.asked == []
    assert tight.counts()["Patterns counted"] == 2
    assert MAX_TREND_EVENTS == 8

    quiet = [*steady(), accident("C1", "2026-07-20")]
    _, without_a_rise = measured(quiet, merged(quiet).links)
    assert (without_a_rise.groups, without_a_rise.candidates) == (4, 2)
    assert sorted(item.value for item in without_a_rise.trends) == ["flood", "village:sholavandan"]
    assert "vehicle_accident" not in "; ".join(item.value for item in without_a_rise.trends)


def test_the_request_prints_the_windows_it_measured_and_the_events_it_shows():
    incidents = steady()
    links = merged(incidents).links
    found = candidate(incidents, links, TrendDimension.INCIDENT_TYPE)
    request = trend_request(found, deduplicated=True)

    assert request.system == TREND_RULES
    assert request.json_schema == TREND_SCHEMA
    assert request.meta == {"trend_id": found.trend_id, "prompt_version": "trend-1"}
    for line in (
        "1. 2026-07-13..2026-08-11: 1 distinct event(s) from 3 incident(s) in 3 record(s) of "
        "3 publisher(s); the run held 3 record(s) of any kind there",
        "baseline average 1.00 event(s), busiest baseline window 1, recent window 3, change +2.00, "
        "recent over baseline 3.00 times",
        "An alert would need at least 3 distinct event(s) in the recent window and a rise of 2.00 "
        "times its baseline average",
        "these events are 1.00 per record read recently against 0.33 in its baseline, 3.00 times",
        "Recent event 1 (",
        f"date: {RECENT[2]} (from event_time)",
        "incidents: INC-NEWS-R30",
        "places: Sholavandan (village)",
        "category: not stated",
        "Pattern: incident_type = flood",
        "The recent window holds 3 distinct event(s) and 3 are printed here",
        "Repeated reports of one happening were merged into one event before counting.",
    ):
        assert line in request.user
    assert "No link decisions were available" not in request.user

    plain = trend_request(found, deduplicated=False).user
    assert "No link decisions were available, so two records may describe one happening" in plain
    assert "were merged into one event before counting" not in plain


def test_more_events_than_one_request_holds_are_said_to_be_cut_without_losing_numbers(monkeypatch):
    incidents = steady()
    links = merged(incidents).links
    _, full = measured(incidents, links)
    assert full.truncated == 0 and MAX_TREND_EVENTS == 8

    monkeypatch.setattr(trends_module, "MAX_TREND_EVENTS", 2)
    found = candidate(incidents, links, TrendDimension.INCIDENT_TYPE)
    assert (len(found.shown), found.truncated) == (2, 1)
    assert "1 further event(s) fall in the same window but were not shown to you." in trend_request(
        found, deduplicated=True).user
    assert "Recent event 3 (" not in trend_request(found, deduplicated=True).user

    _, cut = measured(incidents, links)
    assert (cut.truncated, cut.candidates) == (2, 2)
    assert any("2 event(s) sit in a recent window but were beyond the 2 printed" in line
               for line in cut.limitations())
    for trend in cut.trends:
        assert trend.shown_events == 2 and trend.events == 3


def test_an_answer_that_names_an_event_it_never_saw_is_recorded_and_dropped():
    incidents = steady()
    links = merged(incidents).links
    _, report = measured(incidents, links, reading(cited_events=[1, 2, 99]))

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    found = candidate(incidents, links, TrendDimension.INCIDENT_TYPE)
    assert "the reading named event 99, which was not shown to it" in trend.findings
    assert trend.status is TrendStatus.POTENTIAL
    assert trend.reading.cited_event_ids == [unit.event_id for unit in found.shown[:2]]
    assert set(trend.reading.cited_event_ids) <= set(trend.event_ids)


def test_generated_wording_that_claims_a_confirmation_is_never_an_alert():
    incidents = steady()
    for wording in CLAIM_WORDS:
        _, report = measured(
            incidents,
            merged(incidents).links,
            reading(pattern=f"The rise in the recent window is {wording} by these counts."),
        )
        trend = of(report, TrendDimension.INCIDENT_TYPE)
        assert trend.status is TrendStatus.POTENTIAL
        assert any(f"the wording says {wording!r}" in item for item in trend.findings)

    assert claim_words_in(["a rise may fit the monsoon", None]) == []
    assert claim_words_in(["The flood was caused by heavy rain"]) == ["caused by"]


def test_an_alert_needs_events_that_carry_a_quote_from_their_own_record():
    incidents = steady()
    _, report = measured(incidents, merged(incidents).links)
    trend = of(report, TrendDimension.INCIDENT_TYPE)

    assert evidence_rules(trend.arithmetic, trend.references, None) == []
    bare = [
        item.model_copy(update={"evidence_ids": [], "quotes": []}) for item in trend.references
    ]
    assert evidence_rules(trend.arithmetic, bare, None) == [
        "only 0 of its events carry a quote from their own record, and an alert needs 3"
    ]

    document = trend.model_dump(mode="json")
    document["references"][0]["evidence_ids"] = []
    document["references"][0]["quotes"] = []
    with pytest.raises(ValidationError, match="does not meet every requirement") as refused:
        Trend.model_validate(document)
    assert "carry a quote from their own record, and an alert needs 3" in str(refused.value)


def test_a_reading_that_says_the_events_are_different_problems_is_not_one_pattern():
    incidents = steady()
    _, report = measured(incidents, merged(incidents).links, reading(one_problem=False))

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert trend.status is TrendStatus.POTENTIAL
    assert any("the events shown are different problems" in item for item in trend.findings)
    assert trend.reading.one_problem is False


@pytest.mark.parametrize(("confidence", "finding"), [(0.4, "held at confidence 0.40, below 0.8"),
                                                     (None, "gave confidence None; 0.5 was used")])
def test_a_shaky_or_missing_confidence_is_named_and_never_quietly_raised(confidence, finding):
    incidents = steady()
    _, report = measured(incidents, merged(incidents).links, reading(confidence=confidence))

    trend = of(report, TrendDimension.INCIDENT_TYPE)
    assert trend.status is TrendStatus.POTENTIAL
    assert any(finding in item for item in trend.findings)
    assert trend.reading.confidence == (0.4 if confidence else 0.5)
    assert TREND_THRESHOLD == 0.8


def test_a_model_that_refuses_or_answers_nonsense_still_leaves_the_numbers_measured():
    incidents = steady()
    links = merged(incidents).links

    def refuse(request: LLMRequest) -> dict[str, Any]:
        raise LLMError("the endpoint said no")

    judge, failed = measured(incidents, links, refuse)
    trend = of(failed, TrendDimension.INCIDENT_TYPE)
    assert (failed.asked, failed.not_interpreted) == (0, 2)
    assert len(failed.failed) == 2 and "the endpoint said no" in failed.failed[0]
    assert trend.reading is None and trend.provider == NOT_ASKED
    assert any("no interpretation was available: the endpoint said no" in item
               for item in trend.findings)
    assert failed.counts()["Measured without a reading"] == 2

    def nonsense(request: LLMRequest) -> dict[str, Any]:
        return {"cited_events": "the first two"}

    _, unusable = measured(incidents, links, nonsense)
    assert unusable.trends and "is not a reading" in unusable.failed[0]

    def exhausted(request: LLMRequest) -> dict[str, Any]:
        raise QuotaExhaustedError("daily tokens used up")

    _, deferred = measured(incidents, links, exhausted)
    assert deferred.stop_reason and "daily tokens" in deferred.stop_reason
    assert deferred.not_interpreted == 2 and deferred.asked == 0
    assert "no interpretation was available: daily tokens used up" in deferred.trends[0].findings[0]
    assert "no interpretation was asked: daily tokens used up" in deferred.trends[1].findings[0]
    assert all(item.status is TrendStatus.POTENTIAL and item.reading is None
               for item in deferred.trends)
    assert deferred.counts()["Measured without a reading"] == 2


def test_the_run_interprets_a_bounded_number_of_candidates_and_measures_the_rest(monkeypatch):
    incidents = steady()
    links = merged(incidents).links
    judge, report = measured(incidents, links)
    assert report.candidates == 2 and MAX_TREND_REQUESTS == 6
    assert judge.asked and report.not_interpreted == 0

    monkeypatch.setattr(trends_module, "MAX_TREND_REQUESTS", 1)
    limited = Answering(reading())
    budgeted = emerging_trends(
        incidents,
        chain_factory=lambda: Chain.single(limited),
        links=links,
        now=NOW,
    )

    assert (budgeted.asked, budgeted.not_interpreted) == (1, 1)
    assert budgeted.candidates == 2
    silent = [item for item in budgeted.trends if item.reading is None][0]
    assert "this run interprets at most 1 candidate increase(s)" in silent.findings[0]
    assert len(limited.asked) == 1
    assert budgeted.counts()["Measured without a reading"] == 1


def test_a_saved_recurring_issue_becomes_a_pattern_the_windows_count():
    incidents = steady()
    links = merged(incidents)
    grouped = recurring_issues(
        incidents, chain_factory=lambda: Chain.single(Answering(grouping())), links=links.links,
        now=NOW)
    assert len(grouped.accepted) == 1

    judge, report = measured(incidents, links.links, families=grouped.accepted)

    assert report.candidates == 3
    family = grouped.accepted[0]
    trend = [item for item in report.trends if item.dimension is TrendDimension.ISSUE_FAMILY][0]
    assert (trend.value, trend.family_id, trend.title) == (
        family.family_id.lower(), family.family_id, family.label)
    assert trend.events == 3
    assert any("Pattern: issue_family = " + family.family_id.lower() in request.user
               for request in judge.asked)
    assert not any("no saved recurring issue" in line for line in report.limitations())

    _, alone = measured(incidents, links.links)
    assert any("no saved recurring issue was available" in line for line in alone.limitations())


def test_events_with_no_date_and_events_outside_the_windows_are_counted_as_left_out():
    wide = [
        *steady(),
        flooding("EARLY", "2026-07-05"),
        undated(flooding("BLANK", RECENT[1])),
    ]
    _, windowed = measured(wide, merged(wide).links, filters=Filters(since=date(2026, 7, 1)))
    assert (windowed.outside, windowed.undated) == (1, 0)
    assert windowed.counts()["Events outside the windows"] == 1
    assert any("fall before the earliest window or after the recent one" in line
               for line in windowed.limitations())

    _, plain = measured(wide, merged(wide).links)
    assert plain.undated == 1
    assert plain.counts()["Events with no date"] == 1
    assert any("1 distinct event(s) carry no date" in line for line in plain.limitations())
    for report in (windowed, plain):
        for trend in report.trends:
            assert "NEWS-EARLY0" not in trend.record_ids
            assert "NEWS-BLANK0" not in trend.record_ids


def test_an_empty_or_undated_dataset_measures_no_windows_and_builds_no_model():
    stream = io.StringIO()
    found = emerging_trends([], chain_factory=never, now=NOW)
    print_trends(found, stream=stream)
    printed = stream.getvalue()

    assert (found.eligible, found.units, found.groups, found.candidates, found.asked) == (
        0, 0, 0, 0, 0)
    assert found.windows.blocked and "no event the run counted carries a date" in found.windows.blocked
    assert f"Emerging trends ({TREND_PROMPT_VERSION}, {TREND_PROMPT_DIGEST}" in printed
    assert "Candidate increases: 0" in printed and "Alert-eligible trends: 0" in printed
    assert "an empty window says nothing about a district" in printed

    blank = emerging_trends([undated(flooding("X", RECENT[1]))], chain_factory=never, now=NOW)
    assert (blank.eligible, blank.undated, blank.candidates) == (1, 1, 0)


def test_a_trend_is_the_numbers_it_prints_and_cannot_be_edited_into_an_alert():
    incidents = steady()
    links = merged(incidents).links
    _, report = measured(incidents, links)
    trend = of(report, TrendDimension.INCIDENT_TYPE)
    document = trend.model_dump(mode="json")

    assert Trend.model_validate(document).trend_id == trend.trend_id

    document["events"] = 4
    with pytest.raises(ValidationError, match="is not the 3 event id\\(s\\) it lists"):
        Trend.model_validate(document)
    document["events"] = 3

    document["trend_id"] = "TRD-" + "0" * 16
    with pytest.raises(ValidationError, match="is not the one its window and events derive"):
        Trend.model_validate(document)
    document["trend_id"] = trend.trend_id

    document["findings"] = ["a person decided it was fine"]
    with pytest.raises(ValidationError, match="carries a finding, so it is not eligible"):
        Trend.model_validate(document)

    _, mild_report = measured(mild(), merged(mild()).links)
    held = of(mild_report, TrendDimension.INCIDENT_TYPE)
    edited = held.model_dump(mode="json")
    edited.update({"findings": [], "status": "alert_eligible", "review": "accepted"})
    with pytest.raises(ValidationError, match="does not meet every requirement"):
        Trend.model_validate(edited)


def test_the_arithmetic_rebuilds_its_windows_and_refuses_a_number_it_cannot_trace():
    incidents = steady()
    _, report = measured(incidents, merged(incidents).links)
    arithmetic = of(report, TrendDimension.INCIDENT_TYPE).arithmetic.model_dump(mode="json")

    moved = json.loads(json.dumps(arithmetic))
    moved["blocks"][0]["since"] = (date(2026, 7, 12)).isoformat()
    moved["blocks"][0]["days"] = 31
    with pytest.raises(ValidationError, match="the windows are not comparable"):
        TrendArithmetic.model_validate(moved)

    apart = json.loads(json.dumps(arithmetic))
    apart["blocks"][1]["since"] = (date(2026, 8, 13)).isoformat()
    apart["blocks"][1]["until"] = (date(2026, 9, 11)).isoformat()
    apart["blocks"][1]["days"] = 30
    with pytest.raises(ValidationError, match="not back to back"):
        TrendArithmetic.model_validate(apart)

    shifted = json.loads(json.dumps(arithmetic))
    shifted["baseline_mean"] = 0.5
    with pytest.raises(ValidationError, match="not the mean of the baseline windows"):
        TrendArithmetic.model_validate(shifted)

    counting = json.loads(json.dumps(arithmetic))
    counting["blocks"][2].update({"events": 4, "event_ids": counting["blocks"][2]["event_ids"]})
    with pytest.raises(ValidationError, match="is not the 3 event id\\(s\\) it lists"):
        TrendBlock.model_validate(counting["blocks"][2])


def test_the_saved_trends_are_reused_until_the_window_or_the_events_change(tmp_path):
    incidents = steady()
    links = merged(incidents).links

    first_judge, first = measured(incidents, links, store=TrendStore(tmp_path).load())
    second_judge, second = measured(incidents, links, store=TrendStore(tmp_path).load())

    assert (first.asked, first.reused) == (2, 0)
    assert (second.asked, second.reused) == (0, 2) and second_judge.asked == []
    assert [item.trend_id for item in second.trends] == [item.trend_id for item in first.trends]
    assert [item.findings for item in second.trends] == [item.findings for item in first.trends]

    lines = (tmp_path / TRENDS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    summary = json.loads((tmp_path / TREND_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert sorted(summary) == sorted(
        ("schema_version", "prompt_version", "prompt_digest", "filters", "windows", "counts",
         "limitations", "trends"))
    assert summary["prompt_digest"] == TREND_PROMPT_DIGEST
    assert summary["windows"]["spans"][0] == ["2026-07-13", "2026-08-11"]
    assert summary["counts"]["Alert-eligible trends"] == 2

    stale = json.loads(lines[0])
    stale["prompt_digest"] = "0" * 16
    (tmp_path / TRENDS_FILENAME).write_text("\n".join([json.dumps(stale), lines[1]]) + "\n",
                                            encoding="utf-8")
    _, reread = measured(incidents, links, store=TrendStore(tmp_path).load())
    assert reread.reused == 1 and reread.asked == 1

    wider = [
        *incidents,
        flooding("R4", "2026-10-08"),
    ]
    judge, grown = measured(wider, merged(wider).links, store=TrendStore(tmp_path).load())
    assert grown.candidates == 2 and grown.reused == 0 and len(judge.asked) == 2


def test_a_damaged_trends_file_stops_the_run_instead_of_being_overwritten(tmp_path):
    (tmp_path / TRENDS_FILENAME).write_text('{"trend_id": "TRD-x"}\n', encoding="utf-8")

    with pytest.raises(CheckpointError, match="line 1 is not a trend this pipeline wrote"):
        TrendStore(tmp_path).load()
    assert (tmp_path / TRENDS_FILENAME).read_text(encoding="utf-8") == '{"trend_id": "TRD-x"}\n'


def test_the_report_prints_the_windows_the_reading_and_what_holds_a_trend_back():
    stream = io.StringIO()
    incidents = mild()
    _, report = measured(incidents, merged(incidents).links)

    print_trends(report, detailed=True, stream=stream)
    printed = stream.getvalue()

    assert f"Emerging trends ({TREND_PROMPT_VERSION}, {TREND_PROMPT_DIGEST}, incident contract 2.0)" in printed
    assert "Candidate increases: 2" in printed and "Potential trends: 2" in printed
    assert "incident_type=flood" in printed and "place=village:sholavandan" in printed
    assert "3 distinct event(s) in 2026-09-11..2026-10-10 against an average of 2.00, 1.50x baseline" in printed
    assert "potential (confidence 0.90)" in printed
    assert "1.50 times its baseline average, under the 2.00 threshold" in printed
    assert "reading: வெள்ளம் மீண்டும் வந்தது - Three happenings" in printed
    assert "may fit instead: The monsoon may have returned" in printed
    assert "(a reading of the numbers, not a record)" in printed
    assert "    2026-07-13..2026-08-11: 2 event(s), 3 incident(s), 3 record(s)" in printed
    assert "places: Sholavandan (village)" in printed
    assert "at village precision" in printed
    assert printed.index("reading:") < printed.index("may fit instead")
    assert "limitation: a counted increase is arithmetic" in printed

    brief = io.StringIO()
    print_trends(report, stream=brief)
    assert "may fit instead" not in brief.getvalue()


def water_told(request: LLMRequest) -> dict[str, Any]:
    answer = water_answer()
    answer.pop("event_time")
    return answer


def cli_records() -> list[dict[str, Any]]:
    days = ("2026-07-13", "2026-08-20", RECENT[0], RECENT[1], RECENT[2])
    return [
        waterlogging(
            record_id=f"NEWS-MDU-WLR-{index:04d}",
            source_id=PUBLISHERS[index % 3],
            event_time=f"{day}T08:30:00",
            source_url=f"https://www.dinamalar.com/news/madurai/rain/{index}",
            raw_reference=f"https://www.dinamalar.com/news/madurai/rain/{index}",
        )
        for index, day in enumerate(days)
    ]


def cli_answer(request: LLMRequest) -> dict[str, Any]:
    if "trend_id" in request.meta:
        return reading()(request)
    return water_told(request)


def test_the_pipeline_measures_trends_and_reuses_them_next_run(tmp_path, capsys):
    records = cli_records()
    provider = Answering(cli_answer)
    argv = ["--all", "--emerging-trends", "--state-dir", str(tmp_path)]

    code = main(argv, provider=provider, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert f"Emerging trends ({TREND_PROMPT_VERSION}" in out
    assert "Windows: recent 2026-09-11..2026-10-10" in out
    assert "Distinct events dated: 5" in out and "Candidate increases: 2" in out
    assert "Interpreted now: 2" in out and "Alert-eligible trends: 0" in out
    assert "no link decisions were available" in out
    assert len([request for request in provider.asked if "trend_id" in request.meta]) == 2

    second = Answering(cli_answer)
    code = main(argv, provider=second, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "Reused decisions: 2" in out and "Interpreted now: 0" in out
    assert not [request for request in second.asked if "trend_id" in request.meta]

    assert (tmp_path / TRENDS_FILENAME).exists() and (tmp_path / TREND_SUMMARY_FILENAME).exists()


def test_the_pipeline_counts_only_events_a_saved_link_decision_merged(tmp_path, capsys):
    records = [
        waterlogging(),
        waterlogging(record_id="NEWS-MDU-WLR-0002",
                     source_url="https://www.dinamalar.com/news/madurai/rain/4338981"),
    ]

    def linked(request: LLMRequest) -> dict[str, Any]:
        if "link_id" in request.meta:
            return judging()(request) | {"verdict": "same_event", "confidence": 0.92,
                                         "evidence_a": shown_ids(request, "A")[:2],
                                         "evidence_b": shown_ids(request, "B")[:2]}
        return water_told(request)

    code = main(["--all", "--link-events", "--state-dir", str(tmp_path)],
                provider=Answering(linked), environ={}, transport=responder(listing(*records)))
    assert code == EXIT_OK, capsys.readouterr().out

    quiet = Answering(never_asked)
    code = main(["--all", "--emerging-trends", "--state-dir", str(tmp_path)],
                provider=quiet, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "Distinct events dated: 1" in out and "Candidate increases: 0" in out
    assert "no link decisions were available" not in out
    assert "no increase was measured" in out
    assert quiet.asked == []


def never_asked(request: LLMRequest) -> dict[str, Any]:
    raise AssertionError("one event in one window is no comparison, so nothing may be asked")


def test_the_pipeline_reports_a_blocked_history_without_inventing_a_zero(capsys):
    records = cli_records()[:3]

    code = main(["--all", "--emerging-trends"], provider=Answering(cli_answer), environ={},
                transport=responder(listing(*records)))
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "Candidate increases: 0" in out and "Alert-eligible trends: 0" in out
    assert "so no increase was measured" in out


def test_a_trend_failure_and_a_deferral_change_the_exit_code(tmp_path, capsys):
    records = cli_records()

    def both(captured) -> str:
        return captured.out + captured.err

    def refused(request: LLMRequest) -> dict[str, Any]:
        if "trend_id" in request.meta:
            raise QuotaExhaustedError("daily tokens used up")
        return water_told(request)

    code = main(["--all", "--emerging-trends", "--state-dir", str(tmp_path)],
                provider=Answering(refused), environ={}, transport=responder(listing(*records)))
    printed = both(capsys.readouterr())
    assert code == EXIT_DEFERRED, printed
    assert "daily tokens used up" in printed
    assert "Stopped: daily tokens used up" in printed

    broken = tmp_path / TRENDS_FILENAME
    broken.write_text('{"trend_id": "TRD-x"}\n', encoding="utf-8")
    code = main(["--all", "--emerging-trends", "--state-dir", str(tmp_path)],
                provider=Answering(cli_answer), environ={}, transport=responder(listing(*records)))
    printed = both(capsys.readouterr())
    assert code == EXIT_CHECKPOINT, printed
    assert "is not a trend this pipeline wrote" in printed
    assert "the saved trends were not changed" in printed
    assert broken.read_text(encoding="utf-8") == '{"trend_id": "TRD-x"}\n'

    def partial(request: LLMRequest) -> dict[str, Any]:
        if "trend_id" in request.meta:
            raise LLMError("the endpoint said no")
        return water_told(request)

    fresh = tmp_path / "second"
    code = main(["--all", "--emerging-trends", "--state-dir", str(fresh)],
                provider=Answering(partial), environ={}, transport=responder(listing(*records)))
    printed = both(capsys.readouterr())
    assert code == EXIT_PARTIAL, printed
    assert "the endpoint said no" in printed
    assert "no interpretation was available: the endpoint said no" in printed


def test_the_trend_settings_are_refused_without_the_flag_that_uses_them(capsys):
    for argv in (
        ["--all", "--recent-days", "10"],
        ["--all", "--baseline-blocks", "4"],
        ["--all", "--min-events", "5"],
        ["--all", "--min-rise", "1.5"],
        ["--all", "--coverage-drift", "2"],
        ["--all", "--place", "Madurai"],
        ["--all", "--emerging-trends", "--recent-days", "0"],
        ["--all", "--emerging-trends", "--recent-days", "400"],
        ["--all", "--emerging-trends", "--baseline-blocks", "1"],
        ["--all", "--emerging-trends", "--min-events", "1"],
        ["--all", "--emerging-trends", "--min-rise", "0.5"],
        ["--all", "--emerging-trends", "--coverage-drift", "0.5"],
    ):
        with pytest.raises(SystemExit):
            main(argv, environ={})
        assert "usage:" in capsys.readouterr().err
