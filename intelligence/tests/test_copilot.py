from __future__ import annotations

import io
import json
import re
from typing import Any, Callable

import pytest
from pydantic import ValidationError

from intelligence.chat import GENERAL_PROMPT_VERSION, ROUTE_PROMPT_VERSION
from intelligence.checkpoint import CheckpointError
from intelligence.contract import Incident, PriorityLevel, RecordKind, ReviewState
from intelligence.copilot import (
    ANSWER_PROMPT_DIGEST,
    ANSWER_PROMPT_VERSION,
    ANSWER_RULES,
    ANSWER_SCHEMA,
    ANSWERS_FILENAME,
    ASK_RULES,
    ASK_SCHEMA,
    ASK_THRESHOLD,
    COPILOT_PROMPT_DIGEST,
    COPILOT_PROMPT_VERSION,
    COPILOT_SUMMARY_FILENAME,
    MAX_DAYS_BACK,
    MAX_EVENTS_SHOWN,
    MAX_PLACES_PRINTED,
    MAX_QUESTIONS_PER_RUN,
    MAX_STATEMENTS,
    MIN_QUOTED_EVENTS,
    NO_CORPUS,
    NOT_A_RECORD_QUESTION,
    Answer,
    AnswerState,
    AnswerStore,
    Interpretation,
    InterpretationDraft,
    QuestionIntent,
    QuestionScript,
    StatementKind,
    answer_id_for,
    ask_copilot,
    ask_lines,
    build_interpretation,
    clean,
    corpus_of,
    digit_runs,
    event_block,
    print_answers,
    rows_of,
    script_of,
    search,
    unknown_digits,
)
from intelligence.fallback import Chain
from intelligence.llm import LLMError, LLMRequest, QuotaExhaustedError
from intelligence.linking import eligible, link_incidents
from intelligence.pipeline import (
    ENV_API_BASE_URL,
    EXIT_CHECKPOINT,
    EXIT_DEFERRED,
    EXIT_OK,
    EXIT_PARTIAL,
    main,
)
from intelligence.recurrence import Filters, recurring_issues
from intelligence.tests.builders import listing, responder, water_answer
from intelligence.tests.record_fixtures import waterlogging
from intelligence.tests.test_linking import NOW, Answering
from intelligence.tests.test_recurrence import flood, supply, undated
from intelligence.tests.test_trends import RECENT, merged, reading, steady
from intelligence.trends import emerging_trends

TA_ASK = "சோழவந்தானில் வெள்ளம் மீண்டும் வந்த பகுதிகள் எவை?"
EN_ASK = "Which areas have reported repeated flooding?"
MIXED_ASK = "In Sholavandan, what days reported வெள்ளம்?"
FLOODED = "சோழவந்தான் கிராமத்தில் வைகை ஆற்று வெள்ளம் 40 வீடுகளை மூழ்கடித்தது"
COUNTED = "ஒரே ஊரில் வெள்ளம் பல முற பதிவாகியுளளது"


def three() -> list[Incident]:
    return [
        flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02"),
        flood("NEWS-B", "செப்டம்பர் 14 அன்று", "2026-09-14"),
        flood("NEWS-C", "செப்டம்பர் 28 அன்று", "2026-09-28"),
    ]


def printed(request: LLMRequest) -> dict[int, list[str]]:
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
    return per


def read(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "intent": "repeated_areas",
        "places": ["சோழவந்தான்"],
        "understood": "சோழவந்தானில் மீண்டும் வந்த வெள்ளம் தேடப்பட்டது",
        "confidence": 0.9,
    }
    return body | changes


def statement(text: str, kind: str, events: list[int], evidence: list[str]) -> dict[str, Any]:
    return {"text": text, "kind": kind, "events": events, "evidence": evidence, "confidence": 0.9}


def said(**changes: Any) -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        per = printed(request)
        events = sorted(per)
        quoted = [per[item][0] for item in events if per[item]]
        body: dict[str, Any] = {
            "headline": "சோழவந்தானில் பல முற வெள்ளம் பதிவாகியுளளது",
            "statements": [
                statement(COUNTED, "calculated", events, []),
                statement(FLOODED, "source_fact", events[:1], quoted[:1]),
            ],
            "caveats": [],
            "events": events,
            "evidence": quoted,
            "saved": [],
            "confidence": 0.9,
        }
        return body | changes

    return answer


def never() -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        raise AssertionError("no answer may be asked here")

    return answer


def answering(
    question_read: Any,
    answer_said: Any,
    background: Any = None,
    route: Any = None,
) -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        version = request.meta.get("prompt_version")
        if version == ROUTE_PROMPT_VERSION:
            decided = {"needs_records": True, "general": False, "confidence": 0.9}
            if route is None:
                return decided
            return route(request) if callable(route) else route
        if version == COPILOT_PROMPT_VERSION:
            return question_read(request) if callable(question_read) else question_read
        if version == ANSWER_PROMPT_VERSION:
            return answer_said(request) if callable(answer_said) else answer_said
        if version == GENERAL_PROMPT_VERSION:
            body: dict[str, Any] = {
                "answerable": False,
                "district_claims": False,
                "headline": "",
                "lines": [],
                "needs_records": "the question is about this district and only its records answer it",
                "confidence": 0.9,
            }
            if background is None:
                return body
            return background(request) if callable(background) else background
        raise AssertionError(f"the copilot sent a request from prompt {version!r}")

    return answer


def asked(
    incidents: Any,
    questions: Any,
    question_read: Any = None,
    answer_said: Any = None,
    **fields: Any,
) -> tuple[Answering, Any]:
    judge = Answering(
        answering(
            question_read if question_read is not None else read(),
            answer_said if answer_said is not None else never(),
        )
    )
    return judge, ask_copilot(
        incidents, questions, chain_factory=lambda: Chain.single(judge), now=NOW, **fields
    )


def versions(judge: Answering) -> list[Any]:
    return [request.meta.get("prompt_version") for request in judge.asked]


def asked_versions(judge: Answering) -> list[str]:
    return [item for item in versions(judge) if item in (COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION)]


def interpretation_of(draft: InterpretationDraft, pool: Any, **fields: Any) -> Any:
    corpus = fields.pop("corpus", corpus_of(eligible(pool), []))
    anchor = fields.pop("anchor", corpus.latest)
    return build_interpretation(
        EN_ASK,
        script_of(EN_ASK),
        draft,
        vocabulary=corpus.vocabulary,
        anchor=anchor,
        extras=[],
        provider="scripted",
        model="gpt-oss-120b",
        shown="h" * 64,
        decided_at=NOW,
        **fields,
    )


def test_the_two_copilot_prompts_are_pinned_and_ask_for_no_number_of_its_own():
    assert (COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION) == ("copilot-6", "answer-1")
    assert COPILOT_PROMPT_DIGEST == "d72ed86219d64762"
    assert ANSWER_PROMPT_DIGEST == "20ba0d754c1c4750"
    for phrase in (
        "The question may be Tamil, English or a mix of both",
        "counted back from the newest dated record the pipeline holds",
        "Never write a date yourself",
        "general when the question asks nothing about what these records would carry, whatever language it "
        "is written in",
        "A question about how things stand in this district, its situation, what is going on here or what "
        "needs attention is about these records and never general",
        "set follow_up true and read the two questions as one",
        "category, incident_type, department, min_severity and min_priority take one token",
    ):
        assert phrase in ASK_RULES
    for phrase in (
        "Never write a number that is not printed above",
        "A source_fact is what a record says and must cite the numbered evidence printed with it",
        "Never invent an index, an identifier, a place, a date or a source",
        "A statement you cannot cite is not an answer",
        "Do not stretch a nearby fact into an answer",
        "Never write that something is confirmed, proved, statistically significant, or caused by",
        "Answer in the language the question is written in - Tamil, English, or Tamil written in Latin",
    ):
        assert phrase in ANSWER_RULES
    assert sorted(ASK_SCHEMA["properties"]) == [
        "category", "compare", "confidence", "days_back", "department", "follow_up", "incident_type",
        "intent", "min_priority", "min_severity", "places", "understood",
    ]
    assert sorted(ANSWER_SCHEMA["properties"]) == [
        "caveats", "confidence", "events", "evidence", "headline", "saved", "statements",
    ]
    assert "days" not in json.dumps(ANSWER_SCHEMA) and "total" not in json.dumps(ASK_SCHEMA)
    judge, _ = asked(three(), [EN_ASK], read(), never())
    ask = judge.asked[0]
    assert "No record content is printed here: set how to look, do not answer." in ask.user
    assert "சோழவந்தான் கிராமத்தில் வைகை" not in ask.user
    assert ask.meta == {"answer_id": answer_id_for(EN_ASK), "prompt_version": COPILOT_PROMPT_VERSION}


def test_the_read_is_shown_the_question_that_came_before_it():
    judge, _ = asked(three(), [EN_ASK], read(), said(), previous="and what about the roads?")
    lines = judge.asked[0].user.splitlines()
    assert lines[0] == f"Question: {EN_ASK}"
    assert lines[1] == "Earlier in this conversation, the same person asked: and what about the roads?"
    assert lines[2] == "The question uses latin characters."
    plain = asked(three(), [EN_ASK], read(), said())[0].asked[0].user
    assert "Earlier in this conversation" not in plain
    blank = asked(three(), [EN_ASK], read(), said(), previous="   ")[0].asked[0].user
    assert blank == plain


def test_a_read_that_declares_a_follow_up_is_one_and_a_silent_read_is_not():
    declared = interpretation_of(
        InterpretationDraft.model_validate(read(follow_up=True)), three()
    )
    assert declared.follow_up is True
    silent = interpretation_of(InterpretationDraft.model_validate(read()), three())
    assert silent.follow_up is False
    refused = interpretation_of(None, three())
    assert refused.follow_up is False


def test_a_tamil_an_english_and_a_mixed_question_are_labelled_as_written():
    assert script_of(TA_ASK) is QuestionScript.TAMIL
    assert script_of(EN_ASK) is QuestionScript.LATIN
    assert script_of(MIXED_ASK) is QuestionScript.MIXED
    assert script_of("1234 5678") is QuestionScript.OTHER
    judge, report = asked(three(), [MIXED_ASK], read(), said())
    answer = report.answers[0]
    assert answer.script is QuestionScript.MIXED
    assert answer.interpretation.script is QuestionScript.MIXED
    assert clean("  A ") == "A" and clean(None) == ""


def test_a_number_written_in_tamil_digits_is_the_same_number_for_the_guard():
    assert digit_runs("௧௨ வீடுகள், 34096 மீட்டர்") == ["12", "34096"]
    assert digit_runs("007 metres") == ["7"]
    assert digit_runs(None) == []
    allowed = set(digit_runs("The search matched 3 distinct event(s)"))
    assert unknown_digits("3 houses", allowed) == []
    assert unknown_digits("12 houses in 2030", allowed) == ["12", "2030"]


def test_the_question_sets_only_the_window_it_asks_for_against_the_newest_record():
    corpus = corpus_of(eligible(three()), [])
    assert corpus.window() == "2026-08-02..2026-09-28 (newest from event_time)"
    week = interpretation_of(InterpretationDraft(**read(days_back=7)), three())
    assert (week.filters.since, week.filters.until) == (
        __import__("datetime").date(2026, 9, 22),
        __import__("datetime").date(2026, 9, 28),
    )
    assert week.review is ReviewState.ACCEPTED and week.days_back == 7
    month = interpretation_of(InterpretationDraft(**read(days_back=30, places=["Sholavandan"])), three())
    assert month.describe() == "from 2026-08-30 to 2026-09-28; place matching any of 'Sholavandan'"
    beyond = interpretation_of(InterpretationDraft(**read(days_back=MAX_DAYS_BACK + 1)), three())
    assert beyond.filters.since is None and beyond.filters.until is None and beyond.days_back is None
    assert any("not between 1 and 365" in line for line in beyond.findings)
    assert beyond.review is ReviewState.REVIEW_REQUIRED
    no_dates = interpretation_of(
        InterpretationDraft(**read(days_back=30)),
        three(),
        corpus=corpus_of(eligible([undated(item) for item in three()]), []),
        anchor=None,
    )
    assert no_dates.days_back is None
    assert any("no record here carries a date to count back from" in line for line in no_dates.findings)


def test_a_tamil_question_finds_the_place_the_records_write_in_tamil():
    judge, report = asked(three(), [TA_ASK], read(), said())
    answer = report.answers[0]
    assert answer.interpretation.places_matched == ["சோழவந்தான்"]
    assert answer.events == 3 and answer.incidents == 3 and answer.records == 3
    assert [area.name for area in answer.areas] == ["Sholavandan"]
    assert answer.geographic_precision == "village"
    assert answer.state is AnswerState.GROUNDED and answer.findings == []
    assert answer.missing is None
    assert answer.review is ReviewState.ACCEPTED
    assert [item.kind for item in answer.statements] == [
        StatementKind.CALCULATED,
        StatementKind.SOURCE_FACT,
    ]
    assert answer.quoted_events >= MIN_QUOTED_EVENTS
    assert answer.quoted_events == 3 and len(answer.citations) == 3
    assert len(answer.followups) == 3 and answer.recorded == []
    assert answer.numbers and set(answer.numbers) >= {"3", "2026"}
    assert answer.explanation is not None
    assert answer.explanation.shown_hash == answer.shown_hash


def test_an_english_or_mixed_question_about_the_same_place_finds_the_same_events():
    _, tamil = asked(three(), [TA_ASK], read(), said())
    _, english = asked(three(), [EN_ASK], read(places=["Sholavandan"]), said())
    _, mixed = asked(three(), [MIXED_ASK], read(places=["Sholavandan"]), said())
    assert english.answers[0].event_ids == tamil.answers[0].event_ids
    assert mixed.answers[0].event_ids == tamil.answers[0].event_ids
    assert english.answers[0].answer_id == answer_id_for(EN_ASK)
    assert len({item.answer_id for item in [*tamil.answers, *english.answers, *mixed.answers]}) == 3


def test_the_search_is_deterministic_and_asks_no_model_to_retrieve():
    corpus = corpus_of(eligible([*three(), supply(record_id="NEWS-SUP")]), [])
    pool = [*three(), supply(record_id="NEWS-SUP")]
    windowed = interpretation_of(
        InterpretationDraft(**read(days_back=30, places=["சோழவந்தான்"])), pool, corpus=corpus
    )
    first = search(corpus, windowed, [])
    again = search(corpus, windowed, [])
    assert [item.event_id for item in first.units] == [item.event_id for item in again.units]
    assert first.event_ids == sorted(first.event_ids)
    assert len(first.units) == 2 and first.record_ids == ["NEWS-B", "NEWS-C"]
    assert first.recent is not None and first.recent.events == 2
    assert first.truncated == 0 and first.empty_reason is None
    elsewhere = interpretation_of(
        InterpretationDraft(**read(places=["கருங்காலக்குடி"])), pool, corpus=corpus
    )
    moved = search(corpus, elsewhere, [])
    assert moved.record_ids == ["NEWS-SUP"] and len(moved.units) == 1
    assert moved.undated == 0 and moved.anchor is not None
    missing = interpretation_of(InterpretationDraft(**read(places=["Perungudi"])), pool, corpus=corpus)
    empty = search(corpus, missing, [])
    assert not empty.units and empty.empty_reason is not None


def test_a_place_no_record_names_is_answered_as_a_gap_and_costs_no_answer_call():
    judge, report = asked(three(), [EN_ASK], read(places=["Perungudi"]), never())
    answer = report.answers[0]
    assert answer.state is AnswerState.NOT_ANSWERABLE
    assert answer.events == 0 and answer.citations == [] and answer.statements == []
    assert answer.missing is not None and "Perungudi" in answer.missing
    assert any("no record here names the place" in line for line in answer.findings)
    assert answer.explanation is None and answer.provider == "no reading was asked"
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION]
    assert report.not_asked == 1 and report.failed == [] and report.stop_reason is None


def test_a_question_read_below_the_bar_is_answered_but_held_for_a_person():
    judge, report = asked(three(), [EN_ASK], read(confidence=0.4), said())
    answer = report.answers[0]
    assert answer.interpretation.review is ReviewState.REVIEW_REQUIRED
    assert any(f"below the {ASK_THRESHOLD}" in line for line in answer.findings)
    assert answer.state is AnswerState.QUALIFIED
    assert answer.review is ReviewState.REVIEW_REQUIRED
    assert answer.missing is None and answer.events == 3
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION]


def test_a_comparison_needs_a_window_before_it_compares_anything():
    held = interpretation_of(InterpretationDraft(**read(compare=True)), three())
    assert held.compare is False and held.days_back is None
    assert any("comparison with no window length to compare" in line for line in held.findings)
    with pytest.raises(ValidationError, match="needs a window length"):
        Interpretation(
            question=EN_ASK,
            compare=True,
            confidence=0.9,
            review=ReviewState.ACCEPTED,
            understood="x",
            provider="scripted",
            model="gpt-oss-120b",
            prompt_digest=COPILOT_PROMPT_DIGEST,
            shown_hash="h" * 64,
            decided_at=NOW,
        )


def test_a_comparison_counts_both_windows_and_the_change_between_them():
    judge, report = asked(
        three(),
        ["What changed this week compared with last week?"],
        read(intent="period_change", days_back=30, compare=True, places=["சோழவந்தான்"]),
        said(),
    )
    answer = report.answers[0]
    assert answer.previous is not None and answer.previous.events == 1
    assert answer.change == answer.events - answer.previous.events
    assert answer.comparison == (
        "2 distinct event(s) in 2026-08-30..2026-09-28 against 1 in 2026-07-31..2026-08-29, "
        "a change of +1 event(s)"
    )
    assert answer.state is AnswerState.GROUNDED
    assert "Comparison: " in judge.asked[-1].user
    _, over = asked(
        [undated(item) for item in three()],
        [EN_ASK],
        read(days_back=14, compare=True, places=["சோழவந்தான்"]),
        said(),
    )
    gap = over.answers[0]
    assert gap.interpretation.compare is False and gap.interpretation.days_back is None
    assert any("no window length to compare, so nothing was compared" in line for line in gap.findings)
    assert any("no record here carries a date to count back from" in line for line in gap.findings)
    assert gap.previous is None and gap.change is None and gap.comparison is None


def test_the_search_is_a_set_of_filters_and_never_a_filter_string_of_its_own():
    with pytest.raises(ValidationError, match="never through a filter string"):
        Interpretation(
            question=EN_ASK,
            filters=Filters(place="Sholavandan"),
            confidence=0.9,
            review=ReviewState.ACCEPTED,
            understood="x",
            provider="scripted",
            model="gpt-oss-120b",
            prompt_digest=COPILOT_PROMPT_DIGEST,
            shown_hash="h" * 64,
            decided_at=NOW,
        )
    with pytest.raises(ValidationError, match="only come from a window counted in days"):
        Interpretation(
            question=EN_ASK,
            filters=Filters(since=NOW.date()),
            confidence=0.9,
            review=ReviewState.ACCEPTED,
            understood="x",
            provider="scripted",
            model="gpt-oss-120b",
            prompt_digest=COPILOT_PROMPT_DIGEST,
            shown_hash="h" * 64,
            decided_at=NOW,
        )
    guessed = interpretation_of(
        InterpretationDraft(**read(intent="not_an_intent", incident_type="not_a_type")), three()
    )
    assert guessed.intent is QuestionIntent.OTHER
    assert guessed.filters.incident_type is None
    assert any("is not one of the tokens" in line for line in guessed.findings)
    assert any("which the contract does not have" in line for line in guessed.findings)
    assert guessed.review is ReviewState.REVIEW_REQUIRED


def test_a_severity_a_category_and_a_department_the_question_names_narrow_the_search():
    corpus = [*three(), supply(record_id="NEWS-SUP")]
    _, report = asked(
        corpus,
        [EN_ASK],
        read(intent="department_load", places=["கருங்காலக்குடி"], department="water_resources",
             category="water", min_severity="moderate"),
        said(),
    )
    answer = report.answers[0]
    assert answer.events == 1 and answer.state is AnswerState.GROUNDED
    assert answer.interpretation.filters.department is not None
    assert answer.interpretation.filters.min_severity is not None
    judge, unstaffed = asked(
        corpus, [EN_ASK], read(places=["சோழவந்தான்"], department="police"), never()
    )
    assert unstaffed.answers[0].state is AnswerState.NOT_ANSWERABLE
    assert any("no accepted incident matches" in line for line in unstaffed.answers[0].findings)
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION]


def test_a_priority_the_question_names_narrows_to_the_records_that_stated_one():
    corpus = [*three(), supply(record_id="NEWS-SUP", priority="high", priority_quote="நான்கு வாரமாக")]
    judge, report = asked(
        corpus,
        [EN_ASK],
        read(places=["கருங்காலக்குடி"], min_priority="high"),
        said(),
    )
    answer = report.answers[0]
    assert answer.interpretation.filters.min_priority is PriorityLevel.HIGH
    assert answer.events == 1 and answer.state is AnswerState.GROUNDED
    assert "priority at least high" in answer.interpretation.filters.describe()
    assert "priorities here: high" in judge.asked[0].user.splitlines()
    assert "priority: high" in judge.asked[1].user.splitlines()
    _, silent = asked(corpus, [EN_ASK], read(places=["கருங்காலக்குடி"], min_priority="critical"), never())
    assert silent.answers[0].state is AnswerState.NOT_ANSWERABLE
    assert any("no accepted incident matches" in line for line in silent.answers[0].findings)
    broad, shown = asked(corpus, [EN_ASK], read(places=["சோழவந்தான்"]), said())
    assert "priorities here: high" in broad.asked[0].user.splitlines()
    assert "priority: not stated" in broad.asked[1].user.splitlines()
    assert shown.answers[0].interpretation.filters.min_priority is None
    bare, _ = asked(three(), [EN_ASK], read(), said())
    assert "priorities here: none" in bare.asked[0].user.splitlines()


def test_a_record_that_is_not_an_incident_is_never_answered_from():
    note = flood("NEWS-NOTE", "ஆகஸ்ட் 2 அன்று", "2026-08-02").model_copy(
        update={"record_kind": RecordKind.CONTEXT}
    )
    judge, report = asked([*three(), note], [TA_ASK], read(), said())
    assert report.incidents == 3 and report.units == 3
    assert note.incident_id not in report.answers[0].incident_ids
    assert report.answers[0].records == 3


def test_a_record_waiting_for_a_person_is_left_out_of_every_search():
    waiting = undated_like(flood("NEWS-D", "அக்டோபர் 5 அன்று", "2026-10-05"))
    judge, report = asked([*three(), waiting], [TA_ASK], read(), said())
    answer = report.answers[0]
    assert report.skipped_review == 1
    assert waiting.incident_id not in answer.incident_ids
    assert any("waiting for a person" in line for line in report.limitations())
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION]


def undated_like(incident: Incident) -> Incident:
    copy = incident.model_copy(deep=True)
    copy.validation.state = ReviewState.REVIEW_REQUIRED
    copy.validation.issues = []
    return copy


def test_repeated_reports_of_one_happening_are_answered_as_one_event():
    pair = [
        flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02"),
        flood("NEWS-B", "ஆகஸ்ட் 2 அன்று", "2026-08-02"),
    ]
    _, apart = asked(pair, [EN_ASK], read(places=["Sholavandan"]), said())
    assert apart.answers[0].events == 2 and apart.deduplicated is False
    assert any("no link decisions were available" in line for line in apart.limitations())
    links = link_incidents(
        pair, chain_factory=lambda: Chain.single(Answering(merged_judge())), now=NOW
    )
    _, together = asked(pair, [EN_ASK], read(places=["Sholavandan"]), said(), links=links.links)
    assert together.answers[0].events == 1
    assert together.deduplicated is True
    assert together.answers[0].incidents == 2 and together.answers[0].records == 2
    assert any("merged into one event before counting" in line for line in together.limitations()) is False
    judge, _ = asked(pair, [EN_ASK], read(places=["Sholavandan"]), said(), links=links.links)
    assert "Repeated reports of one happening were merged into one event" in judge.asked[-1].user


def merged_judge() -> Callable[[LLMRequest], dict[str, Any]]:
    from intelligence.tests.test_trends import judging

    return judging()


def test_an_undated_event_answers_no_question_about_a_period_and_asks_no_answer():
    loose = undated(supply(record_id="NEWS-UND"))
    judge, report = asked(
        [*three(), loose], [EN_ASK], read(days_back=30, places=["கருங்காலக்குடி"]), never()
    )
    answer = report.answers[0]
    assert report.undated == 1
    assert answer.events == 0 and answer.undated == 1
    assert answer.missing is not None and "carry no date" in answer.missing
    assert any("carry no date" in line for line in answer.findings)
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION]
    assert report.not_asked == 1


def test_a_window_over_records_that_carry_no_date_is_dropped_and_said_so():
    loose = [undated(item) for item in three()]
    judge, report = asked(loose, [EN_ASK], read(days_back=30), said())
    answer = report.answers[0]
    assert answer.interpretation.days_back is None
    assert answer.events == 3 and answer.undated == 3
    assert answer.state is AnswerState.QUALIFIED
    assert any("no record here carries a date to count back from" in line for line in answer.findings)
    assert answer.review is ReviewState.REVIEW_REQUIRED
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION]


def test_more_events_than_one_request_holds_are_printed_as_a_cut():
    many = [
        flood(f"NEWS-{index}", "செப்டம்பர் 20 அன்று", "2026-09-20")
        for index in range(MAX_EVENTS_SHOWN + 3)
    ]
    judge, report = asked(many, [EN_ASK], read(places=["Sholavandan"]), said())
    answer = report.answers[0]
    assert answer.events == MAX_EVENTS_SHOWN + 3
    assert answer.shown_events == MAX_EVENTS_SHOWN and answer.truncated_events == 3
    assert report.truncated == 3
    assert any("fell beyond the" in line for line in report.limitations())
    request = judge.asked[-1]
    assert "are printed here" in request.user and "say what you were not shown" in request.user
    assert request.user.count("Event ") == MAX_EVENTS_SHOWN


def test_a_corpus_naming_more_places_than_one_request_prints_the_first_of_them():
    corpus = corpus_of(eligible(three()), [])
    names = sorted(f"place {index}" for index in range(MAX_PLACES_PRINTED + 5))
    corpus.vocabulary = names
    text = "\n".join(ask_lines(EN_ASK, script_of(EN_ASK), corpus, [], []))
    head = f"Places these records name, as they write them (first {MAX_PLACES_PRINTED} of "
    assert f"{head}{len(names)}): " in text
    assert names[MAX_PLACES_PRINTED - 1] in text and names[-1] not in text
    small = corpus_of(eligible(three()), [])
    assert head not in "\n".join(ask_lines(EN_ASK, script_of(EN_ASK), small, [], []))


def test_the_answer_sees_only_the_records_the_search_kept():
    corpus = [*three(), supply(record_id="NEWS-SUP")]
    judge, report = asked(corpus, [TA_ASK], read(), said())
    user = judge.asked[-1].user
    assert "NEWS-SUP" not in user and "கருங்காலக்குடி" not in user
    assert report.answers[0].record_ids == ["NEWS-A", "NEWS-B", "NEWS-C"]


def test_an_empty_corpus_never_sends_a_question_to_a_model():
    judge, report = asked([], [EN_ASK], never(), never())
    assert judge.asked == []
    assert report.not_asked == 1 and report.incidents == 0
    answer = report.answers[0]
    assert answer.state is AnswerState.NOT_ANSWERABLE
    assert answer.missing == NO_CORPUS and answer.provider == "no reading was asked"
    assert report.limitations()[0] == NO_CORPUS


def test_only_the_numbers_a_measured_line_printed_may_be_repeated():
    judge, report = asked(
        three(),
        [EN_ASK],
        read(),
        said(statements=[
            statement("99999 houses were submerged in 2030", "calculated", [1, 2], []),
            statement("௩ நிகழ்வுகள்", "calculated", [1, 2, 3], []),
        ]),
    )
    answer = report.answers[0]
    assert [item.text for item in answer.statements] == ["௩ நிகழ்வுகள்"]
    assert sorted(answer.statements[0].event_ids) == answer.event_ids
    assert any("no measured line printed for this question carries" in line for line in answer.findings)
    assert answer.state is AnswerState.QUALIFIED
    payload = answer.model_dump(mode="json")
    with pytest.raises(ValidationError, match="no measured line printed"):
        Answer.model_validate(payload | {"numbers": []})


def test_a_source_fact_needs_its_own_quotation_and_an_invented_one_is_named():
    judge, report = asked(
        three(),
        [EN_ASK],
        read(),
        said(statements=[
            statement("no record says this", "source_fact", [1], []),
            statement("a quote that was never printed", "source_fact", [1], ["ev-not-printed"]),
        ]),
    )
    answer = report.answers[0]
    assert answer.statements == []
    assert answer.quoted_events == 3
    assert answer.state is AnswerState.NOT_ANSWERABLE
    assert any("without citing a quotation printed with it" in line for line in answer.findings)
    assert any("not one of the quotations printed" in line for line in answer.findings)
    assert answer.explanation is not None


def test_an_answer_naming_an_event_or_a_quotation_it_was_not_given_is_refused():
    judge, report = asked(three(), [EN_ASK], read(), said(events=[99], evidence=["ev-nothing"]))
    answer = report.answers[0]
    assert answer.explanation is not None
    assert answer.explanation.cited_event_ids == [] and answer.explanation.cited_evidence_ids == []
    assert any("names event 99, which was not printed" in line for line in answer.findings)
    assert any("not one of the quotations printed" in line for line in answer.findings)
    payload = answer.model_dump(mode="json")
    stray = [dict(item, event_ids=["EVT-0000000000000000"]) for item in payload["statements"]]
    with pytest.raises(ValidationError, match="never retrieved"):
        Answer.model_validate(payload | {"statements": stray})
    kept = payload["statements"][1]["evidence_ids"]
    thin = [item for item in payload["citations"] if item["evidence_id"] not in kept]
    with pytest.raises(ValidationError, match="carries no quotation for"):
        Answer.model_validate(payload | {"citations": thin})


def test_an_answer_that_says_reports_prove_something_is_never_ground():
    judge, report = asked(
        three(),
        [EN_ASK],
        read(),
        said(statements=[statement("The flooding was caused by a blocked drain", "calculated",
                                   [1, 2, 3], [])]),
    )
    answer = report.answers[0]
    assert any("'caused by'" in line and "never establishes" in line for line in answer.findings)
    assert answer.state is AnswerState.QUALIFIED and answer.review is ReviewState.ACCEPTED
    payload = answer.model_dump(mode="json") | {"state": "grounded", "findings": []}
    with pytest.raises(ValidationError):
        Answer.model_validate(payload)


def test_a_reading_and_a_suggestion_are_kept_apart_from_a_fact():
    judge, report = asked(
        three(),
        [EN_ASK],
        read(),
        said(statements=[
            statement(COUNTED, "calculated", [1, 2, 3], []),
            statement("The monsoon may have returned.", "possible_explanation", [1], []),
            statement("Clear the drain before the next month.", "recommendation", [1], []),
            statement("a wrong kind", "guess", [1], []),
        ]),
    )
    answer = report.answers[0]
    kinds = [item.kind.value for item in answer.statements]
    assert kinds == ["calculated", "possible_explanation", "recommendation"]
    assert [item.review.value for item in answer.statements] == [
        "accepted",
        "review_required",
        "review_required",
    ]
    assert any("is not one of: source_fact, calculated, possible_explanation, recommendation" in line
               for line in answer.findings)
    assert answer.state is AnswerState.QUALIFIED and answer.review is ReviewState.REVIEW_REQUIRED
    printed = io.StringIO()
    print_answers(report, stream=printed)
    out = printed.getvalue()
    assert "a suggestion for a person, never recorded as done" in out
    assert "a reading of the numbers, not a record" in out


def test_a_saved_recurring_issue_and_a_measured_increase_are_citable_by_id():
    grouped = recurring_issues(three(), chain_factory=lambda: Chain.single(Answering(grouping())),
                               now=NOW)
    family = grouped.accepted[0]
    judge, report = asked(three(), [EN_ASK], read(), said(saved=[family.family_id]),
                          families=grouped.accepted)
    answer = report.answers[0]
    assert answer.families_cited == [family.family_id] and answer.trends_cited == []
    assert report.families == 1
    assert f"Saved recurring issue {family.family_id}" in judge.asked[-1].user
    assert "recurring issues already grouped from these records" in judge.asked[0].user

    pool = steady()
    trend_judge = Answering(reading())
    measured = emerging_trends(pool, chain_factory=lambda: Chain.single(trend_judge),
                               links=merged(pool).links, now=NOW)
    certified = [item for item in measured.trends if item.review is ReviewState.ACCEPTED]
    assert len(certified) == 2
    judge, report = asked(pool, [EN_ASK], read(), said(saved=[certified[0].trend_id]),
                          measured=certified)
    answer = report.answers[0]
    assert answer.trends_cited == [certified[0].trend_id] and answer.families_cited == []
    assert f"Measured increase {certified[0].trend_id}" in judge.asked[-1].user
    assert "increases already measured over these records" in judge.asked[0].user
    assert "Measured increase" not in judge.asked[0].user


def grouping() -> Callable[[LLMRequest], dict[str, Any]]:
    from intelligence.tests.test_recurrence import grouping as group

    return group()


def test_an_identifier_the_pipeline_never_printed_is_named_as_a_guess():
    judge, report = asked(three(), [EN_ASK], read(), said(saved=["ISS-not-real", "TRD-nothing"]))
    answer = report.answers[0]
    assert answer.families_cited == [] and answer.trends_cited == []
    assert sum("neither a saved recurring issue nor a measured increase" in line
               for line in answer.findings) == 2
    assert answer.state is AnswerState.QUALIFIED


def test_more_statements_than_the_answer_keeps_are_cut():
    many = [statement(f"every event reported, reading {chr(65 + index)}", "calculated", [1, 2, 3], [])
            for index in range(MAX_STATEMENTS + 3)]
    judge, report = asked(three(), [EN_ASK], read(), said(statements=many))
    answer = report.answers[0]
    assert len(answer.statements) == MAX_STATEMENTS
    assert [item.text for item in answer.statements] == [item["text"] for item in many[:MAX_STATEMENTS]]
    assert any(f"only the first {MAX_STATEMENTS} are kept" in line for line in answer.findings)


def test_a_run_answers_only_the_questions_it_budgets():
    judge, report = asked(
        three(), [EN_ASK, TA_ASK, MIXED_ASK], read(), said(), max_requests=2
    )
    assert report.read == 2 and report.asked == 2 and report.not_asked == 1
    assert report.max_requests == 2
    third = report.answers[-1]
    assert third.state is AnswerState.NOT_ANSWERABLE
    assert third.missing is not None and "reads at most 2 question(s)" in third.missing
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION] * 2


def test_a_seventh_question_in_one_run_is_refused_rather_than_asked():
    questions = [f"Question {index} about flooding in Sholavandan" for index in range(
        MAX_QUESTIONS_PER_RUN + 1
    )]
    judge, report = asked(three(), questions, read(places=["Sholavandan"]), said())
    assert report.read == MAX_QUESTIONS_PER_RUN == 6
    assert len(report.answers) == MAX_QUESTIONS_PER_RUN + 1
    assert report.answers[-1].state is AnswerState.NOT_ANSWERABLE
    assert report.answers[-1].missing is not None and "reads at most 6" in report.answers[-1].missing
    assert any("were never put to a provider" in line for line in report.limitations())


def test_a_blank_or_repeated_question_is_dropped_before_anything_is_asked():
    judge, report = asked(three(), [EN_ASK, f"  {EN_ASK}  ", "", "   "], read(), said())
    assert report.questions == [EN_ASK]
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION]


def test_answers_are_reused_while_both_shown_hashes_hold(tmp_path):
    judge, first = asked(three(), [EN_ASK, TA_ASK], read(), said(),
                         store=AnswerStore(tmp_path).load())
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION] * 2
    assert first.reused == 0 and first.read == 2 and first.asked == 2
    assert (tmp_path / ANSWERS_FILENAME).exists() and (tmp_path / COPILOT_SUMMARY_FILENAME).exists()

    judge, second = asked(three(), [EN_ASK, TA_ASK], read(), said(),
                          store=AnswerStore(tmp_path).load())
    assert asked_versions(judge) == []
    assert second.reused == 2 and second.reused_read == 2 and second.read == 0
    assert [item.answer_id for item in second.answers] == [item.answer_id for item in first.answers]

    summary = json.loads((tmp_path / COPILOT_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert summary["prompt_version"] == COPILOT_PROMPT_VERSION
    assert summary["prompt_digest"] == COPILOT_PROMPT_DIGEST
    assert summary["answer_prompt_digest"] == ANSWER_PROMPT_DIGEST
    assert summary["counts"]["Answers reused"] == 2
    assert len(summary["answers"]) == 2


def test_a_different_question_or_a_new_record_invalidates_the_saved_answer(tmp_path):
    asked(three(), [EN_ASK], read(), said(), store=AnswerStore(tmp_path).load())
    judge, wider = asked([*three(), flood("NEWS-D", "அக்டோபர் 5 அன்று", "2026-10-05")], [EN_ASK],
                         read(), said(), store=AnswerStore(tmp_path).load())
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION]
    assert wider.reused == 0 and wider.reused_read == 0 and wider.answers[0].events == 4

    judge, other = asked(three(), ["Which departments were told about Sholavandan flooding?"],
                         read(), said(), store=AnswerStore(tmp_path).load())
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION]
    assert other.reused == 0


def test_a_damaged_answer_file_stops_the_run_and_is_left_alone(tmp_path):
    asked(three(), [EN_ASK], read(), said(), store=AnswerStore(tmp_path).load())
    saved = tmp_path / ANSWERS_FILENAME
    saved.write_text('{"answer_id": "ANS-x"}\n', encoding="utf-8")
    untouched = saved.read_bytes()
    with pytest.raises(CheckpointError, match="is not an answer this pipeline wrote"):
        AnswerStore(tmp_path).load()
    assert saved.read_bytes() == untouched


def test_the_report_prints_the_search_the_answer_and_the_uncertainty():
    judge, report = asked(three(), [TA_ASK], read(), said(caveats=["ஒரே நிகழ்வாக இருக்கலாம்"]))
    brief = io.StringIO()
    print_answers(report, stream=brief)
    out = brief.getvalue()
    assert (
        f"Collector Copilot ({COPILOT_PROMPT_VERSION} + {ANSWER_PROMPT_VERSION}, "
        f"{COPILOT_PROMPT_DIGEST}/{ANSWER_PROMPT_DIGEST}, incident contract 2.0)" in out
    )
    assert f"Q1 [tamil] {TA_ASK}" in out
    assert "searched as: place matching any of 'சோழவந்தான்'" in out
    assert f"understood as: {read()['understood']}" in out
    assert (
        "grounded: 3 distinct event(s) from 3 incident(s) in 3 record(s) of 1 publisher(s), "
        "3 quotation(s) over 3 event(s), village precision, accepted" in out
    )
    assert "open with a quote: 3, action recorded: 0, no status evidenced: 0" in out
    assert "- calculated (accepted, confidence 0.90): " in out
    assert "caveat: ஒரே நிகழ்வாக இருக்கலாம" in out
    assert "limitation: a place is matched by the wording the records themselves use" in out
    assert "not answered:" not in out

    detailed = io.StringIO()
    print_answers(report, detailed=True, stream=detailed)
    deep = detailed.getvalue()
    assert "ev-NEWS-A" in deep and "https://news.example.test/NEWS-A]" in deep
    assert "open INC-NEWS-A (reported):" in deep
    assert len(deep.splitlines()) > len(out.splitlines())

    judge, held = asked(three(), [EN_ASK], read(places=["Perungudi"]), never())
    shown = io.StringIO()
    print_answers(held, stream=shown)
    assert "not answered:" in shown.getvalue()
    assert "finding: no record here names the place" in shown.getvalue()


def test_the_console_answers_two_questions_over_the_records_it_read(tmp_path, capsys):
    records = cli_records()
    judge = Answering(cli_reading())
    argv = ["--all", "--ask", EN_ASK, "--ask", TA_ASK, "--state-dir", str(tmp_path)]
    code = main(argv, provider=judge, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert asked_versions(judge) == [COPILOT_PROMPT_VERSION, ANSWER_PROMPT_VERSION] * 2
    assert "Grounded answers: 2" in out
    assert "district precision" in out
    assert (tmp_path / ANSWERS_FILENAME).exists() and (tmp_path / COPILOT_SUMMARY_FILENAME).exists()

    reuse = Answering(cli_reading())
    code = main(argv, provider=reuse, environ={}, transport=responder(listing(*records)))
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert asked_versions(reuse) == []
    assert "  Reads reused: 2" in out and "  Answers reused: 2" in out

    single = Answering(cli_reading())
    code = main(["--all", "--ask", EN_ASK], provider=single, environ={},
                transport=responder(listing(*records)))
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "grounded: 5 distinct event(s)" in out


def cli_records() -> list[dict[str, Any]]:
    days = ("2026-07-13", "2026-08-20", RECENT[0], RECENT[1], RECENT[2])
    publishers = ("dinamalar", "thinakaran", "musiri")
    return [
        waterlogging(
            record_id=f"NEWS-MDU-WLR-{index:04d}",
            source_id=publishers[index % 3],
            event_time=f"{day}T08:30:00",
            source_url=f"https://www.dinamalar.com/news/madurai/rain/{index}",
            raw_reference=f"https://www.dinamalar.com/news/madurai/rain/{index}",
        )
        for index, day in enumerate(days)
    ]


def cli_reading() -> Callable[[LLMRequest], dict[str, Any]]:
    def answer(request: LLMRequest) -> dict[str, Any]:
        version = request.meta.get("prompt_version")
        if version == COPILOT_PROMPT_VERSION:
            return read(intent="counting", places=["மதுரை"])
        if version == ANSWER_PROMPT_VERSION:
            return said()(request)
        body = water_answer()
        body.pop("event_time")
        return body

    return answer


def test_the_console_refuses_a_question_that_would_set_the_same_search_twice(capsys):
    for argv in (
        ["--all", "--ask", EN_ASK, "--place", "Madurai"],
        ["--all", "--ask", EN_ASK, "--since", "2026-09-01"],
        ["--all", "--ask", EN_ASK, "--category", "water"],
        ["--all", "--ask", EN_ASK, "--min-severity", "high"],
        ["--ask", EN_ASK],
    ):
        with pytest.raises(SystemExit):
            main(argv, environ={})
        printed = capsys.readouterr().err
        assert "usage:" in printed
        assert "--ask" in printed
    with pytest.raises(SystemExit):
        main(["--all", "--ask", EN_ASK, "--place", "Madurai"], environ={})
    assert "the same search twice" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--ask", EN_ASK], environ={})
    assert "say which to read" in capsys.readouterr().err


def test_the_console_defers_when_the_provider_has_no_allowance_left(tmp_path, capsys):
    records = cli_records()

    def refused(request: LLMRequest) -> dict[str, Any]:
        if request.meta.get("prompt_version") == COPILOT_PROMPT_VERSION:
            raise QuotaExhaustedError("daily tokens used up")
        return cli_reading()(request)

    code = main(["--all", "--ask", EN_ASK, "--state-dir", str(tmp_path)],
                provider=Answering(refused), environ={}, transport=responder(listing(*records)))
    printed = capsys.readouterr()
    assert code == EXIT_DEFERRED, printed.out + printed.err
    assert "Stopped: daily tokens used up" in printed.out
    assert "Questions read: 0" in printed.out


def test_the_console_reports_a_partial_run_when_an_answer_fails(tmp_path, capsys):
    records = cli_records()

    def broken(request: LLMRequest) -> dict[str, Any]:
        if request.meta.get("prompt_version") == ANSWER_PROMPT_VERSION:
            raise LLMError("the endpoint said no")
        return cli_reading()(request)

    code = main(["--all", "--ask", EN_ASK, "--state-dir", str(tmp_path)],
                provider=Answering(broken), environ={}, transport=responder(listing(*records)))
    printed = capsys.readouterr()
    assert code == EXIT_PARTIAL, printed.out + printed.err
    assert "no usable answer came back: the endpoint said no" in printed.out
    assert "1 request(s) got no usable answer" in printed.out
    assert "not_answerable:" in printed.out


def test_a_damaged_saved_answer_aborts_the_console_and_keeps_the_file(tmp_path, capsys):
    records = cli_records()
    argv = ["--all", "--ask", EN_ASK, "--state-dir", str(tmp_path)]
    assert main(argv, provider=Answering(cli_reading()), environ={},
                transport=responder(listing(*records))) == EXIT_OK
    capsys.readouterr()
    saved = tmp_path / ANSWERS_FILENAME
    saved.write_text('{"answer_id": "ANS-x"}\n', encoding="utf-8")
    untouched = saved.read_bytes()
    judge = Answering(cli_reading())
    code = main(argv, provider=judge, environ={}, transport=responder(listing(*records)))
    printed = capsys.readouterr()
    assert code == EXIT_CHECKPOINT, printed.out + printed.err
    assert "is not an answer this pipeline wrote" in printed.err
    assert "the saved answers were not changed" in printed.err
    assert saved.read_bytes() == untouched
    assert asked_versions(judge) == []


def general_read(**changes: Any) -> dict[str, Any]:
    return read(
        intent="general", places=[], understood="Not a record question.", confidence=0.95, **changes
    )


def test_a_question_the_model_calls_general_skips_the_search_and_the_answer_call():
    judge, report = asked(three(), ["how many countries are there"], general_read(), never())
    answer = report.answers[0]
    assert answer.interpretation.intent is QuestionIntent.GENERAL
    assert answer.state is AnswerState.NOT_ANSWERABLE
    assert answer.missing == NOT_A_RECORD_QUESTION
    assert answer.events == 0 and answer.citations == [] and answer.statements == []
    assert versions(judge) == [COPILOT_PROMPT_VERSION]
    assert (report.read, report.asked, report.not_asked) == (1, 0, 0)
    assert not any("never put to a provider" in line for line in report.limitations())


def test_a_general_refusal_is_saved_and_reused_because_its_empty_request_hashes_stable(tmp_path):
    judge, first = asked(three(), ["who is the prime minister of india"], general_read(), never(),
                         store=AnswerStore(tmp_path).load())
    assert versions(judge) == [COPILOT_PROMPT_VERSION]
    assert first.answers[0].shown_hash

    judge, again = asked(three(), ["who is the prime minister of india"], general_read(), never(),
                         store=AnswerStore(tmp_path).load())
    assert versions(judge) == []
    assert (again.reused, again.reused_read, again.read, again.asked) == (1, 1, 0, 0)
    assert again.answers[0].missing == NOT_A_RECORD_QUESTION
