from __future__ import annotations

import io
import json
import re
import tempfile
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pytest
from pydantic import ValidationError

from intelligence.chat import (
    CHAT_PROMPT_VERSION,
    COMPOSED_NOTE,
    DEFAULT_SESSION,
    ELABORATION_WORDS,
    GENERAL_NOTE,
    GENERAL_PROMPT_DIGEST,
    GENERAL_PROMPT_VERSION,
    GENERAL_RULES,
    GENERAL_SCHEMA,
    LOOKUP_MODEL,
    LOOKUP_PROVIDER,
    MAX_GENERAL_LINES,
    MAX_MODEL_CALLS_PER_REQUEST,
    MAX_QUESTION_CHARS,
    MONTH_WORDS,
    NO_MODEL_NOTE,
    RELATIVE_DAYS,
    ROUTE_PROMPT_DIGEST,
    ROUTE_PROMPT_VERSION,
    ROUTE_RULES,
    ROUTE_SCHEMA,
    VALUE_ALIASES,
    VALUE_PROMPT_DIGEST,
    VALUE_PROMPT_VERSION,
    VALUE_RULES,
    VALUE_SCHEMA,
    WEEK_WORDS,
    ChatCitation,
    ChatEngine,
    ChatLimits,
    ChatPath,
    ChatReply,
    ChatStatement,
    ChatTopic,
    GeneralDraft,
    Reading,
    RouteDraft,
    ValueDraft,
    composition_fault,
    eligible_values,
    fold,
    general_refusal,
    general_request,
    history_lines,
    matching_values,
    named_places,
    place_vocabulary,
    print_reply,
    read_state_dir,
    record_place,
    reply_id_for,
    route_request,
    said_in,
    topic_of,
    value_confidence,
    value_fields,
    value_request,
)
from intelligence.checkpoint import COMPLETED, FAILED, REVIEW_REQUIRED, Entry
from intelligence.contract import Incident, Issue, IssueCode, RecordKind, ReviewState
from intelligence.copilot import (
    ANSWER_PROMPT_VERSION,
    COPILOT_PROMPT_VERSION,
    MAX_QUESTIONS_PER_RUN,
    TAMIL_HIGH,
    TAMIL_LOW,
    AnswerState,
    AnswerStore,
    QuestionScript,
    StatementKind,
    script_of,
)
from intelligence.fallback import Chain
from intelligence.llm import LLMError, LLMRequest
from intelligence.models.enums import SpanValidation
from intelligence.recurrence import searchable_places
from intelligence.structured import MAPPING_VERSION, process_record
from intelligence.tests.record_fixtures import market_price, weather_forecast
from intelligence.tests.test_copilot import (
    COUNTED,
    EN_ASK,
    FLOODED,
    TA_ASK,
    answering,
    never,
    printed,
    read,
    said,
    statement,
)
from intelligence.tests.test_linking import NOW, Answering
from intelligence.tests.test_recurrence import flood

TOMATO_TA = "மதுரையில் தக்காளி விலை என்ன?"
PADDY_TA = "நெல் விலை என்ன?"
WEATHER_TA = "வானிலை முன்னறிவிப்பு என்ன?"


def price(**fields: Any) -> Incident:
    return process_record(market_price(**fields))


def forecast(**fields: Any) -> Incident:
    return process_record(weather_forecast(**fields))


def onion(**fields: Any) -> Incident:
    return process_record(
        market_price(commodity="Onion", market="K.K. Nagar", market_id="126", min_price=28.0, **fields)
    )


def floods() -> list[Incident]:
    return [
        flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02"),
        flood("NEWS-B", "செப்டம்பர் 14 அன்று", "2026-09-14"),
        flood("NEWS-C", "செப்டம்பர் 28 அன்று", "2026-09-28"),
    ]


def no_chain() -> Chain:
    raise LLMError("no model is configured for this test")


def quiet() -> ChatEngine:
    return ChatEngine(Reading(incidents=[]), chain_factory=no_chain, clock=lambda: NOW)


def values_engine(incidents: Any, **fields: Any) -> ChatEngine:
    fields.setdefault("chain_factory", no_chain)
    fields.setdefault("clock", lambda: NOW)
    return ChatEngine(Reading(incidents=list(incidents)), **fields)


def wording_engine(incidents: Any, wording: Any, **fields: Any) -> tuple[ChatEngine, Answering]:
    judge = Answering(wording)
    fields.setdefault("clock", lambda: NOW)
    engine = ChatEngine(
        Reading(incidents=list(incidents)), chain_factory=lambda: Chain.single(judge), **fields
    )
    return engine, judge


PRINTED_FACT = re.compile(r"^\[(\d+)\] (.*)$", re.M)


def reworded(request: LLMRequest) -> dict[str, Any]:
    """Echo every printed fact back as its own sentence, with wording of its own around it."""
    shown = [(int(number), line) for number, line in PRINTED_FACT.findall(request.user)]
    return {
        "headline": f"As these records put it: {shown[0][1]}",
        "facts": [shown[0][0]],
        "lines": [
            {"text": f"As these records put it: {line}", "facts": [number]}
            for number, line in shown[1:]
        ],
    }


def cycling(*drafts: Any) -> Callable[[Any], Any]:
    queue = list(drafts)

    def draft(request: Any) -> Any:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return draft


def search_engine(
    incidents: Any,
    *,
    question_read: Any = None,
    answer_said: Any = None,
    background: Any = None,
    route: Any = None,
    **fields: Any
) -> tuple[ChatEngine, Answering]:
    judge = Answering(
        answering(
            question_read if question_read is not None else read(),
            answer_said if answer_said is not None else said(),
            background,
            route,
        )
    )
    fields.setdefault("clock", lambda: NOW)
    engine = ChatEngine(
        Reading(incidents=list(incidents)), chain_factory=lambda: Chain.single(judge), **fields
    )
    return engine, judge


def refused(request: Any) -> dict[str, Any]:
    return {
        "headline": "",
        "statements": [],
        "caveats": ["the records shown cannot answer this"],
        "events": [],
        "evidence": [],
        "saved": [],
        "confidence": 0.2,
    }


def background(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "answerable": True,
        "district_claims": False,
        "headline": "A mandi is a market yard where farm produce is bought and sold.",
        "lines": ["Licensed brokers post the day's rates there.", "A committee sets the fee it charges."],
        "needs_records": "",
        "confidence": 0.8,
    }
    return body | changes


def records_route(request: Any = None) -> dict[str, Any]:
    return {"needs_records": True, "general": False, "confidence": 0.9}


def general_route(request: Any = None) -> dict[str, Any]:
    return {"needs_records": False, "general": True, "confidence": 0.9}


def general_reply(**changes: Any) -> ChatReply:
    body: dict[str, Any] = {
        "reply_id": reply_id_for(DEFAULT_SESSION, "what is a mandi committee", None),
        "session_id": DEFAULT_SESSION,
        "question": "what is a mandi committee",
        "script": "latin",
        "topic": ChatTopic.GENERAL,
        "path": ChatPath.GENERAL,
        "state": AnswerState.QUALIFIED,
        "statements": [
            ChatStatement(
                text="A mandi is a market yard where farm produce is bought and sold.",
                kind=StatementKind.POSSIBLE_EXPLANATION,
                review=ReviewState.REVIEW_REQUIRED,
                confidence=0.8,
            )
        ],
        "model_calls": 3,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "prompt_version": GENERAL_PROMPT_VERSION,
        "prompt_digest": GENERAL_PROMPT_DIGEST,
        "asked_at": NOW,
    }
    return ChatReply.model_validate(body | changes)


class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def saved(directory: Path, *entries: Entry) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.jsonl").write_text(
        "".join(item.line() for item in entries), encoding="utf-8"
    )
    return directory


def held_for_review(incident: Incident) -> Incident:
    finding = Issue(
        code=IssueCode.INVALID_VALUE,
        field="data.unit",
        detail="the record states a price without naming the unit it is counted in",
    )
    return incident.model_copy(
        update={
            "validation": incident.validation.model_copy(
                update={"state": ReviewState.REVIEW_REQUIRED, "issues": [finding]}
            )
        }
    )


def entry(incident: Incident, state: str = COMPLETED, **fields: Any) -> Entry:
    body = {
        "record_id": incident.provenance.record_id,
        "state": state,
        "mode": "structured",
        "prompt_version": MAPPING_VERSION,
        "schema_version": incident.schema_version,
        "incident": incident.to_storage_document(),
        "saved_at": NOW.isoformat(),
    }
    return Entry(**(body | fields))


def test_the_topics_the_collector_asks_about_are_recognised_in_both_scripts() -> None:
    asked = {
        "வானிலை முன்னறிவிப்பு என்ன?": ChatTopic.FORECAST,
        "What is tomorrow's temperature?": ChatTopic.FORECAST,
        "மதுரையில் தக்காளி விலை என்ன?": ChatTopic.MARKET,
        "uzhavar santhai onion rate?": ChatTopic.MARKET,
        "சோழவந்தானில் வெள்ளம் எத்தனை முறை?": ChatTopic.RECORDS,
        "How many flood reports came from Sholavandan?": ChatTopic.RECORDS,
    }
    assert {text: topic_of(text) for text in asked} == asked


def test_a_topic_word_hiding_inside_another_word_does_not_route_the_question() -> None:
    assert topic_of("Is the flood risk moderate?") is ChatTopic.RECORDS
    assert topic_of("How many reports fall in this window?") is ChatTopic.RECORDS
    assert topic_of("Will it rain tomorrow?") is ChatTopic.FORECAST
    assert topic_of("மழை பெய்யுமா?") is ChatTopic.FORECAST
    assert topic_of("வெப்பம் எத்தனை?") is ChatTopic.FORECAST
    assert topic_of("What is the onion rate per kg?") is ChatTopic.MARKET


def test_a_tamil_price_question_is_answered_without_any_model_being_asked() -> None:
    reply = values_engine([price(), forecast()]).ask(TOMATO_TA)
    assert reply.path is ChatPath.RECORD_VALUES
    assert reply.topic is ChatTopic.MARKET
    assert reply.state is AnswerState.GROUNDED
    assert reply.model_calls == 0
    assert (reply.provider, reply.model, reply.prompt_version) == (
        LOOKUP_PROVIDER,
        LOOKUP_MODEL,
        "chat-1",
    )
    assert reply.script == "tamil"
    assert reply.headline is not None and reply.headline.startswith("Tomato at Anna nagar")
    assert "₹46–₹50 per kg" in reply.headline
    assert NO_MODEL_NOTE in reply.limitations


def test_a_price_answer_cites_the_field_each_value_was_copied_from() -> None:
    stored = price()
    reply = values_engine([stored]).ask(TOMATO_TA)
    evidence = stored.evidence_by_id()
    values = {fact.field: str(fact.value) for fact in stored.context_facts}
    assert reply.citations
    for item in reply.citations:
        backed = evidence[item.evidence_id]
        assert item.field.startswith("data.")
        assert item.field == backed.field
        assert item.quote == values[item.field]
        assert backed.quote == values[item.field]
        assert (backed.char_start, backed.char_end) == (0, len(values[item.field]))
        assert backed.span_validation is SpanValidation.VALIDATED
        assert item.record_id == stored.provenance.record_id
        assert item.source_id == stored.provenance.source_id
        assert item.source_url == stored.provenance.source_url
        assert item.method.value == "source_metadata"
    assert {item.evidence_id for item in reply.citations} == {
        eid for item in reply.statements for eid in item.evidence_ids
    }


def test_a_forecast_is_answered_as_a_forecast_and_never_as_an_incident() -> None:
    reply = values_engine([forecast(), price()]).ask(WEATHER_TA)
    assert reply.topic is ChatTopic.FORECAST
    assert reply.record_kinds == [RecordKind.FORECAST]
    assert RecordKind.INCIDENT not in reply.record_kinds
    assert {item.record_kind for item in reply.citations} == {RecordKind.FORECAST}
    assert reply.events == 0
    assert reply.statements[0].text.startswith("Madurai (03 Oct 2026): ")
    assert "Multi cloud with thunder lightning and light rain over Madurai district." in (
        reply.statements[0].text
    )
    assert "Max 33.1°C, min 24.5°C." in reply.statements[0].text


def test_a_crop_price_written_in_tamil_names_the_record_that_holds_it() -> None:
    engine = values_engine([onion(), price()])
    assert "onion" in engine.ask("வெங்காயம் விலை என்ன?").statements[0].text.lower()
    assert "tomato" in engine.ask("தக்காளி விலை என்ன?").statements[0].text.lower()


def test_a_paddy_question_when_only_tomato_is_stored_refuses_and_says_what_is_stored() -> None:
    reply = values_engine([price()]).ask(PADDY_TA)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.statements == [] and reply.citations == []
    assert reply.headline is None
    assert "நெல்" in reply.missing
    assert "commodity Tomato" in reply.missing
    assert "நெல்" not in " ".join(item.text for item in reply.statements)


def test_an_older_record_than_the_day_asked_is_named_as_the_records_own_reading(tmp_path: Path) -> None:
    engine = values_engine([price(event_time="2026-10-08T00:00:00")], clock=lambda: NOW)
    reply = engine.ask(TOMATO_TA)
    assert reply.state is AnswerState.GROUNDED
    assert any(
        "the newest price record answering this carries its own date 2026-10-08" in line
        for line in reply.limitations
    )
    fresh = values_engine([price(event_time=NOW.isoformat())], clock=lambda: NOW).ask(TOMATO_TA)
    assert not any("not a reading for the day asked" in line for line in fresh.limitations)
    assert fresh.state is AnswerState.GROUNDED


def test_a_question_naming_a_place_the_records_write_in_another_script_says_so() -> None:
    reply = values_engine([price()]).ask(TOMATO_TA)
    assert reply.state is AnswerState.GROUNDED
    assert any(
        line == "the question named no place these records carry, so records from every place were read"
        for line in reply.limitations
    )


def test_a_question_naming_a_place_the_records_do_carry_narrows_by_it() -> None:
    engine = values_engine([price(), forecast()])
    reply = engine.ask("tomato price in Tamil Nadu")
    assert any(line == "the place the question names: tamil nadu" for line in reply.limitations)
    assert reply.records == 1


def test_a_question_naming_a_place_no_record_here_carries_refuses_naming_both_sides() -> None:
    reply = values_engine([price()]).ask("madurai onion price")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "no record here holds 'onion'" in reply.missing


def test_a_market_named_in_the_records_own_wording_narrows_to_that_market() -> None:
    reply = values_engine([price(), onion()]).ask("What is the onion price at K.K. Nagar?")
    assert reply.records == 1
    assert "K.K. Nagar" in reply.statements[0].text
    assert any(
        line == "the field values the question names: commodity onion; market k.k. nagar"
        for line in reply.limitations
    )


def test_a_mixed_script_question_reports_the_script_it_was_asked_in() -> None:
    reply = values_engine([price()]).ask("Madurai தக்காளி price?")
    assert reply.script == "mixed"
    assert reply.state is AnswerState.GROUNDED


def test_the_printed_records_are_capped_and_the_cap_is_reported() -> None:
    pool = [
        price(),
        onion(),
        price(commodity="Potato", market="Mattakkattiruppi", market_id="127", record_id="AGRI-POTATO"),
    ]
    reply = values_engine(pool, limits=ChatLimits(records_shown=2)).ask("what prices are stored today?")
    assert [item.kind for item in reply.statements] == [
        StatementKind.CALCULATED,
        StatementKind.SOURCE_FACT,
        StatementKind.SOURCE_FACT,
    ]
    assert any("3 price record(s) match the question and 2 are printed" in line for line in reply.limitations)


def test_a_record_waiting_for_a_persons_review_is_never_answered_over() -> None:
    held = held_for_review(price())
    reply = values_engine([held]).ask(TOMATO_TA)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "wait for a person's review" in reply.missing
    assert reply.model_calls == 0
    assert held.incident_id not in reply.record_ids


def test_a_price_question_with_no_price_record_falls_back_to_the_incident_search() -> None:
    engine, judge = search_engine(floods())
    reply = engine.ask(TOMATO_TA)
    assert reply.path is ChatPath.RECORD_SEARCH
    assert any(
        line.startswith("the question asks about price records and none is stored here")
        for line in reply.limitations
    )
    assert reply.record_kinds == [RecordKind.INCIDENT]
    assert len(judge.asked) == 2


def test_a_disaster_question_in_tamil_is_answered_from_incidents_with_quotes() -> None:
    engine, judge = search_engine(floods())
    reply = engine.ask(TA_ASK)
    assert reply.state is AnswerState.GROUNDED
    assert reply.model_calls == 3
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.record_kinds == [RecordKind.INCIDENT]
    assert reply.citations and reply.events > 0
    assert reply.headline and reply.understood
    assert reply.script == "tamil"
    assert [request.meta.get("prompt_version") for request in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        COPILOT_PROMPT_VERSION,
        ANSWER_PROMPT_VERSION,
    ]
    assert reply.prompt_digest == judge.asked[2].meta.get("prompt_digest") or reply.prompt_digest


def test_one_question_per_request_leaves_the_copilot_run_cap_out_of_the_chat() -> None:
    engine, judge = search_engine(floods())
    questions = [
        EN_ASK,
        TA_ASK,
        "How many floods in Sholavandan?",
        "Which areas reported flooding again?",
        "How many water supply reports are there?",
        "Which officials are named in these reports?",
        "How many flood reports came in August?",
        "Which districts do these reports cover?",
    ]
    replies = [engine.ask(item) for item in dict.fromkeys(questions)]
    assert len(replies) > MAX_QUESTIONS_PER_RUN
    assert all(item.model_calls == 3 for item in replies)
    assert len({item.reply_id for item in replies}) == len(replies)
    assert len(judge.asked) == 3 * len(replies)


def test_a_second_question_that_names_nothing_carries_the_first_over_and_says_so() -> None:
    engine, judge = search_engine(
        floods(),
        question_read=cycling(read(), read(places=[], follow_up=True), read()),
        answer_said=said(),
    )
    first = engine.ask(EN_ASK)
    assert first.carried_from is None
    second = engine.ask("and the ones still open?")
    assert second.carried_from == EN_ASK
    assert second.question.startswith(f"{EN_ASK} ")
    assert second.model_calls == 5
    assert any("was read together with the question before it" in line for line in second.limitations)
    assert len(judge.asked) == 8


def forecast_days(count: int) -> list[Incident]:
    days = [(NOW - timedelta(days=offset)).date().isoformat() for offset in range(count)]
    return [
        forecast(
            record_id=f"WEATHER-MDU-{day}",
            title=f"Madurai Weather Forecast - {day}",
            event_time=f"{day}T00:00:00",
        )
        for day in days
    ]


def test_a_request_for_more_of_a_records_answer_prints_every_record_it_holds() -> None:
    engine = values_engine(forecast_days(3))
    first = engine.ask("what is the weather in madurai today?")
    assert first.path is ChatPath.RECORD_VALUES
    assert first.records == 1 and first.carried_from is None and first.model_calls == 0
    again = engine.ask("tell in detail")
    assert again.path is ChatPath.RECORD_VALUES
    assert again.model_calls == 0
    assert again.provider == LOOKUP_PROVIDER and again.model == LOOKUP_MODEL
    assert again.prompt_digest is None
    assert again.carried_from == first.question
    assert again.question == f"{first.question} tell in detail"
    assert again.records == 3 and len(again.statements) == 3
    assert [item.text.split(": ")[0] for item in again.statements] == [
        "Madurai (11 Oct 2026)",
        "Madurai (10 Oct 2026)",
        "Madurai (09 Oct 2026)",
    ]
    assert again.headline == first.headline
    assert len({item.record_id for item in again.citations}) == 3
    assert any("asks for more of that reading" in line for line in again.limitations)
    assert any("this question follows an earlier one" in line for line in again.limitations)


def test_an_answer_over_every_record_it_prints_is_worded_in_one_call() -> None:
    engine, judge = wording_engine(forecast_days(6), reworded)
    first = engine.ask("what is the weather in madurai today?")
    assert first.model_calls == 1 and len(judge.asked) == 1

    again = engine.ask("tell in detail")
    assert again.path is ChatPath.RECORD_VALUES
    assert again.model_calls == 1 and len(judge.asked) == 2
    assert again.prompt_version == VALUE_PROMPT_VERSION and again.prompt_digest == VALUE_PROMPT_DIGEST
    assert (again.provider, again.model) == ("scripted", "gpt-oss-120b")
    assert again.carried_from == first.question
    assert len(again.statements) == 6
    assert again.headline == again.statements[0].text
    assert all(item.text.startswith("As these records put it: ") for item in again.statements)
    assert all(item.evidence_ids for item in again.statements)
    assert len({item.record_id for item in again.citations}) == 6
    assert any(COMPOSED_NOTE in line for line in again.limitations)
    assert not any("no model was asked" in line for line in again.limitations)


def test_a_wording_that_leaves_one_printed_record_out_is_dropped_whole() -> None:
    def partial(request: LLMRequest) -> dict[str, Any]:
        body = reworded(request)
        body["lines"] = body["lines"][:-1]
        return body

    engine, judge = wording_engine([price(), onion()], partial)
    reply = engine.ask("tomato and onion price in madurai?")
    plain = values_engine([price(), onion()]).ask("tomato and onion price in madurai?")
    assert reply.model_calls == 1 and len(judge.asked) == 1
    assert [item.text for item in reply.statements] == [item.text for item in plain.statements]
    assert any("wording was dropped" in line and "unwritten" in line for line in reply.limitations)


def test_a_wording_that_joins_every_fact_into_one_sentence_is_dropped_whole() -> None:
    def joined(request: LLMRequest) -> dict[str, Any]:
        shown = [line for _, line in PRINTED_FACT.findall(request.user)]
        return {"headline": "; ".join(shown), "facts": list(range(1, len(shown) + 1)), "lines": []}

    engine, judge = wording_engine(forecast_days(4), joined)
    engine.ask("what is the weather in madurai today?")
    reply = engine.ask("tell in detail")
    plain = values_engine(forecast_days(4))
    plain.ask("what is the weather in madurai today?")
    unworded = plain.ask("tell in detail")
    assert reply.model_calls == 1 and len(judge.asked) == 2
    assert [item.text for item in reply.statements] == [item.text for item in unworded.statements]
    assert any("wording was dropped" in line and "into one sentence" in line for line in reply.limitations)


def test_a_wording_that_says_one_sentence_twice_is_dropped_whole() -> None:
    def repeated(request: LLMRequest) -> dict[str, Any]:
        shown = [line for _, line in PRINTED_FACT.findall(request.user)]
        return {"headline": shown[0], "facts": [1], "lines": [{"text": shown[0], "facts": [1]}]}

    engine, judge = wording_engine([forecast()], repeated)
    reply = engine.ask("what is the weather in madurai today?")
    assert reply.model_calls == 1 and len(judge.asked) == 1
    assert len(reply.statements) == 1
    assert any("wording was dropped" in line and "twice" in line for line in reply.limitations)


def test_a_record_that_names_no_place_of_its_own_reads_as_its_narrowest_place() -> None:
    wider = forecast().model_copy(update={"locations": []})
    assert set(searchable_places(wider)) == {"madurai", "tamil nadu"}
    assert record_place(wider) == "madurai"
    assert record_place(wider.model_copy(update={"incident_id": wider.incident_id})) == record_place(wider)


def test_a_follow_up_that_names_its_own_subject_is_answered_on_its_own() -> None:
    engine = values_engine([price(), onion()])
    engine.ask("what is the tomato price?")
    reply = engine.ask("onion price in detail")
    assert reply.carried_from is None
    assert "onion" in reply.statements[0].text.lower()
    assert "tomato" not in " ".join(item.text for item in reply.statements).lower()


def test_a_courtesy_after_a_records_answer_is_routed_and_given_the_question_before_it() -> None:
    engine, judge = search_engine(
        [flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02"), forecast()],
        question_read=read(places=["சோழவந்தான்"]),
        answer_said=said(),
        background=background(),
        route=general_route(),
    )
    weather = engine.ask("what is the weather in madurai today?")
    assert weather.path is ChatPath.RECORD_VALUES and weather.model_calls == 0
    greeting = engine.ask("hi")
    assert greeting.path is ChatPath.GENERAL
    assert greeting.model_calls == 2
    assert [item.meta["prompt_version"] for item in judge.asked] == [
        VALUE_PROMPT_VERSION,
        ROUTE_PROMPT_VERSION,
        GENERAL_PROMPT_VERSION,
    ]
    for request in judge.asked[1:]:
        assert "Asked: what is the weather in madurai today?" in request.user
        assert request.user.splitlines()[-1] == "Question: hi"
    assert "Answered: " in judge.asked[2].user


def test_a_read_that_declares_a_follow_up_carries_even_when_it_named_a_place() -> None:
    engine, _ = search_engine(
        floods(),
        question_read=read(places=["சோழவந்தான்"], follow_up=True),
        answer_said=said(),
    )
    first = engine.ask(EN_ASK)
    assert first.carried_from is None
    again = engine.ask("and in detail?")
    assert again.carried_from == EN_ASK
    assert again.question.startswith(f"{EN_ASK} ")
    assert any("was read together with the question before it" in line for line in again.limitations)


def test_a_question_about_how_the_district_stands_is_searched_and_summarised() -> None:
    engine, judge = search_engine(
        floods(),
        question_read=read(
            intent="other",
            places=[],
            understood="Every accepted incident this run holds was looked at.",
            confidence=0.4,
        ),
        answer_said=said(),
    )
    reply = engine.ask("explain the current situation")
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.topic is ChatTopic.RECORDS
    assert reply.model_calls == 3
    assert reply.statements and reply.records == 3
    assert [item.meta["prompt_version"] for item in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        COPILOT_PROMPT_VERSION,
        ANSWER_PROMPT_VERSION,
    ]


def test_a_fresh_question_that_sets_no_filter_is_answered_on_its_own_wording() -> None:
    engine, _ = search_engine(
        floods(),
        question_read=cycling(read(), read(intent="other", places=[]), read()),
        answer_said=said(),
    )
    engine.ask(EN_ASK)
    again = engine.ask("explain the current situation")
    assert again.carried_from is None
    assert again.question == "explain the current situation"
    assert not any("was read together with the question before it" in line for line in again.limitations)


def test_a_refused_follow_up_is_given_the_whole_conversation_so_far() -> None:
    engine, judge = search_engine(
        floods(), question_read=read(places=["சோழவந்தான்"]), answer_said=refused, background=background()
    )
    assert engine.ask(EN_ASK).path is ChatPath.GENERAL
    again = engine.ask("and in detail?")
    assert again.path is ChatPath.GENERAL
    assert again.carried_from == EN_ASK
    general = [item for item in judge.asked if item.meta.get("prompt_version") == GENERAL_PROMPT_VERSION]
    assert general[0].user == f"Question: {EN_ASK}"
    lines = general[1].user.splitlines()
    assert lines[0] == "Conversation so far, oldest first:"
    assert lines[1] == f"Asked: {EN_ASK}"
    assert lines[2].startswith("Answered: A mandi is a market yard")
    assert lines[-1] == "Question: and in detail?"


def test_the_background_request_is_given_the_question_it_follows() -> None:
    plain = general_request("how many districts are there?")
    assert plain.user == "Question: how many districts are there?"
    assert plain.meta == {"prompt_version": GENERAL_PROMPT_VERSION}
    followed = general_request("tell in detail", history=[("how many districts are there?", "")])
    assert followed.user == (
        "Conversation so far, oldest first:\n"
        "Asked: how many districts are there?\n"
        "\n"
        "Question: tell in detail"
    )
    answered = general_request(
        "in java", history=[("reverse a string", "Use a slice: s[::-1].")]
    ).user
    assert "Asked: reverse a string" in answered
    assert "Answered: Use a slice: s[::-1]." in answered
    for phrase in (
        "claim no company, product or app name as yourself",
        "are this conversation so far: an earlier question and the answer given to it",
    ):
        assert phrase in GENERAL_RULES


def test_a_question_the_records_cannot_answer_is_refused_with_a_reason() -> None:
    engine, _ = search_engine(floods(), question_read=read(), answer_said=never())
    reply = engine.ask(EN_ASK)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.missing and reply.statements == []


def test_a_re_asked_question_is_served_from_the_store_after_one_routing_call(tmp_path: Path) -> None:
    store = AnswerStore(tmp_path / "copilot")
    engine, judge = search_engine(floods(), store=store)
    first = engine.ask(EN_ASK)
    again = engine.ask(EN_ASK)
    assert first.model_calls == 3
    assert again.model_calls == 1
    assert again.reply_id == first.reply_id
    assert len(judge.asked) == 4
    assert [item.meta["prompt_version"] for item in judge.asked[3:]] == [ROUTE_PROMPT_VERSION]
    assert (tmp_path / "copilot" / "answers.jsonl").exists()


def test_an_empty_corpus_refuses_instead_of_answering_from_nothing() -> None:
    engine, judge = search_engine([])
    reply = engine.ask(EN_ASK)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "hold no reading" in reply.missing
    assert [item.meta["prompt_version"] for item in judge.asked] == [ROUTE_PROMPT_VERSION]
    assert reply.model_calls == 1
    assert reply.statements == []


def test_an_empty_corpus_still_answers_a_greeting_from_the_model() -> None:
    engine, judge = search_engine([], background=background(), route=general_route())
    reply = engine.ask("hi")
    assert reply.path is ChatPath.GENERAL
    assert reply.state is AnswerState.QUALIFIED
    assert reply.model_calls == 2
    assert [item.meta["prompt_version"] for item in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        GENERAL_PROMPT_VERSION,
    ]


def test_a_long_question_is_refused_before_anything_is_asked() -> None:
    engine, judge = search_engine(floods())
    reply = engine.ask("வெள்ளம் " * 200)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert f"{MAX_QUESTION_CHARS} characters" in reply.missing or "characters" in reply.missing
    assert "this chat takes at most" in reply.missing
    assert judge.asked == []


def test_an_empty_question_is_refused() -> None:
    reply = values_engine([price()]).ask("   ")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.missing == "no question was asked"


def test_a_session_over_its_request_limit_is_told_the_wait_and_asked_again_after_it() -> None:
    clock = Clock()
    limits = ChatLimits(requests_per_window=2, window_seconds=600)
    engine = values_engine([price()], limits=limits, clock=clock)
    assert engine.ask(TOMATO_TA).state is AnswerState.GROUNDED
    assert engine.ask(TOMATO_TA).state is AnswerState.GROUNDED
    stopped = engine.ask(TOMATO_TA)
    assert stopped.state is AnswerState.NOT_ANSWERABLE
    assert "at most 2 question(s) per 600 seconds" in stopped.missing
    assert "ask again in" in stopped.missing
    assert stopped.model_calls == 0
    clock.advance(601)
    assert engine.ask(TOMATO_TA).state is AnswerState.GROUNDED


def test_sessions_are_kept_apart_from_each_other() -> None:
    limits = ChatLimits(requests_per_window=1, window_seconds=600)
    engine = values_engine([price()], limits=limits)
    assert engine.ask(TOMATO_TA, session_id="a").state is AnswerState.GROUNDED
    assert engine.ask(TOMATO_TA, session_id="b").state is AnswerState.GROUNDED
    assert engine.ask(TOMATO_TA, session_id="a").state is AnswerState.NOT_ANSWERABLE


def test_a_reading_added_after_the_engine_started_answers_the_next_question() -> None:
    engine = quiet()
    assert engine.add([price()]) == 1
    assert engine.add([price()]) == 0
    reply = engine.ask(TOMATO_TA)
    assert reply.state is AnswerState.GROUNDED
    assert reply.records == 1
    assert reply.model_calls == 0


def test_a_reading_added_here_is_counted_among_those_waiting_for_review() -> None:
    engine = quiet()
    assert engine.add([held_for_review(onion())]) == 1
    assert engine.reading.waiting == 1
    assert any("1 record(s) waiting for a person's review" in line for line in engine.reading.note())


def test_the_value_path_asks_one_model_for_wording_alone() -> None:
    engine, judge = wording_engine([price(), onion()], reworded)
    reply = engine.ask("tomato and onion price in madurai?")
    assert reply.path is ChatPath.RECORD_VALUES
    assert reply.model_calls == 1
    assert reply.prompt_version == VALUE_PROMPT_VERSION and reply.prompt_digest == VALUE_PROMPT_DIGEST
    assert (reply.provider, reply.model) == ("scripted", "gpt-oss-120b")
    assert [item.meta["prompt_version"] for item in judge.asked] == [VALUE_PROMPT_VERSION]
    assert reply.headline == reply.statements[0].text
    assert reply.headline.startswith("As these records put it: ")
    plain = values_engine([price(), onion()]).ask("tomato and onion price in madurai?")
    assert reply.statements != plain.statements
    assert len(reply.statements) == 3
    assert all(item.evidence_ids for item in reply.statements)
    assert any(COMPOSED_NOTE in line for line in reply.limitations)
    assert not any(NO_MODEL_NOTE in line for line in reply.limitations)


def test_a_value_answer_without_a_model_left_prints_the_records_own_sentences() -> None:
    engine = values_engine([price()])
    reply = engine.ask(TOMATO_TA)
    assert reply.model_calls == 0
    assert (reply.provider, reply.model) == (LOOKUP_PROVIDER, LOOKUP_MODEL)
    assert reply.prompt_version == CHAT_PROMPT_VERSION and reply.prompt_digest is None
    assert reply.limitations[0] == NO_MODEL_NOTE


def test_a_composed_line_inventing_a_number_is_dropped_and_the_records_sentence_stands() -> None:
    def invented(request: LLMRequest) -> dict[str, Any]:
        body = reworded(request)
        body["headline"] = body["headline"] + " and 999 more quintals"
        return body

    engine, judge = wording_engine([price()], invented)
    reply = engine.ask(TOMATO_TA)
    assert reply.model_calls == 1
    assert not any(item.kind is StatementKind.POSSIBLE_EXPLANATION for item in reply.statements)
    assert all("999" not in item.text for item in reply.statements)
    assert any("wording was dropped" in line and "999" in line for line in reply.limitations)
    assert len(judge.asked) == 1


def test_a_composed_line_putting_a_value_at_a_place_its_own_fact_never_prints_is_dropped() -> None:
    def misplaced(request: LLMRequest) -> dict[str, Any]:
        body = reworded(request)
        last = body["lines"][-1]
        last["text"] = last["text"].replace("Anna nagar", "K.K. Nagar")
        return body

    engine, _ = wording_engine([price(), onion()], misplaced)
    reply = engine.ask("tomato and onion price in madurai?")
    assert reply.model_calls == 1
    assert all("Tomato at K.K. Nagar" not in item.text for item in reply.statements)
    assert any(
        "wording was dropped" in line and "k.k. nagar" in line.casefold()
        for line in reply.limitations
    )


def test_a_composed_line_naming_the_district_no_cited_fact_prints_is_the_records_own_geography() -> None:
    def district(request: LLMRequest) -> dict[str, Any]:
        body = reworded(request)
        body["headline"] = body["headline"] + ", in Madurai"
        return body

    engine, _ = wording_engine([price()], district)
    reply = engine.ask(TOMATO_TA)
    assert reply.model_calls == 1
    assert not any("wording was dropped" in line for line in reply.limitations)
    assert "Madurai" in reply.headline


def test_a_wording_that_moves_a_reading_to_a_place_the_record_never_writes_there_is_dropped() -> None:
    def widened(request: LLMRequest) -> dict[str, Any]:
        body = reworded(request)
        body["headline"] = body["headline"] + ", across Tamil Nadu"
        return body

    engine, _ = wording_engine([price(), forecast()], widened)
    reply = engine.ask("what is the weather in madurai today?")
    assert reply.model_calls == 1
    assert reply.statements[0].text.startswith("Madurai (")
    assert any(
        "wording was dropped" in line and "tamil nadu" in line for line in reply.limitations
    )


def test_a_composed_line_citing_a_fact_never_printed_is_dropped() -> None:
    def off_range(request: LLMRequest) -> dict[str, Any]:
        body = reworded(request)
        body["facts"] = [7]
        return body

    engine, _ = wording_engine([price()], off_range)
    reply = engine.ask(TOMATO_TA)
    assert reply.model_calls == 1
    assert any("not one of the" in line for line in reply.limitations)


def test_a_composed_line_that_rests_on_two_records_is_a_calculation_not_one_records_words() -> None:
    engine, _ = wording_engine([price(), onion()], reworded)
    reply = engine.ask("tomato and onion price in madurai?")
    summary = [item for item in reply.statements if item.kind is StatementKind.CALCULATED]
    assert summary and len({item.record_id for item in reply.citations}) == 2
    assert all(item.evidence_ids for item in reply.statements)


def test_a_composed_value_answer_keeps_the_records_own_confidence() -> None:
    stored = price()
    softer = stored.model_copy(
        update={
            "context_facts": [
                fact.model_copy(update={"confidence": 0.61}) for fact in stored.context_facts
            ]
        }
    )
    plain = values_engine([softer]).ask(TOMATO_TA)
    engine, _ = wording_engine([softer], reworded)
    composed = engine.ask(TOMATO_TA)
    assert [round(item.confidence, 2) for item in composed.statements] == [
        round(item.confidence, 2) for item in plain.statements
    ]


def test_the_value_request_prints_every_fact_numbered_and_the_question_alone() -> None:
    engine, judge = wording_engine([price(), onion()], reworded)
    engine.ask("tomato and onion price in madurai?")
    request = judge.asked[0]
    assert request.user.startswith("Question: tomato and onion price in madurai?")
    assert request.user.splitlines()[1] == "The records' own values, numbered:"
    assert len([line for line in request.user.splitlines() if PRINTED_FACT.match(line)]) == 3
    assert request.meta == {"prompt_version": VALUE_PROMPT_VERSION}


def test_the_value_request_names_the_count_of_facts_the_wording_owes() -> None:
    engine, judge = wording_engine([price(), onion()], reworded)
    engine.ask("tomato and onion price in madurai?")
    printed = [line for line in judge.asked[0].user.splitlines() if PRINTED_FACT.match(line)]
    assert len(printed) > 1
    assert judge.asked[0].user.splitlines()[-1] == (
        f"Write one sentence for each number from 1 to {len(printed)}, citing that number."
    )
    alone, one = wording_engine([price()], reworded)
    alone.ask(TOMATO_TA)
    assert one.asked[0].user.splitlines()[-1] == "Answer in one sentence and leave lines empty."


def test_the_value_prompt_is_pinned_to_one_digest() -> None:
    assert VALUE_PROMPT_VERSION == "value-5"
    assert VALUE_PROMPT_DIGEST == "6c7404bbf5e21ed6"
    for phrase in (
        "Reword those values into prose",
        "keep every number, date and name in the exact form its fact prints it",
        "add, average, round or compare nothing",
        "Write no number that is not printed by a fact you cite",
        "A sentence may name only the places and readings its own cited facts print",
        "several facts may be cited by one sentence when it states each of their own values",
        "it may never replace a printed place with another",
        "is dropped",
        "headline answers the question in one sentence and is never empty",
        "Write a sentence citing every numbered fact printed above",
        "put two facts in one sentence only when that sentence states each of their own values and carries no "
        "more than 3 of them",
        "a sentence that joins more facts than that into one, drops the whole wording",
        "do not call it normal, safe, severe, confirmed or a trend",
        "Answer in the language the question is written in - Tamil, English, or Tamil written in Latin",
    ):
        assert phrase in VALUE_RULES
    assert sorted(VALUE_SCHEMA["properties"]) == ["facts", "headline", "lines"]
    assert "confidence" not in json.dumps(VALUE_SCHEMA)


def test_a_statements_confidence_is_the_records_own_and_is_not_stated_as_certainty() -> None:
    stored = price()
    softer = stored.model_copy(
        update={
            "context_facts": [
                fact.model_copy(update={"confidence": 0.62}) for fact in stored.context_facts
            ]
        }
    )
    assert value_confidence(softer.context_facts) == 0.62
    reply = values_engine([softer]).ask(TOMATO_TA)
    assert reply.statements[0].confidence == 0.62


def test_the_engine_reads_a_saved_state_dir_without_reprocessing_it(tmp_path: Path) -> None:
    directory = saved(tmp_path / "read", entry(price()), entry(forecast()))
    reading = read_state_dir(directory)
    assert len(reading.incidents) == 2
    assert reading.records == 2 and reading.sources == 2
    assert reading.waiting == 0
    assert reading.unreadable == []
    engine = ChatEngine(reading, chain_factory=no_chain)
    reply = engine.ask(TOMATO_TA)
    assert reply.state is AnswerState.GROUNDED
    assert reply.model_calls == 0
    assert any("answered from 2 reading(s) in 2 record(s)" in line for line in reply.standing)
    assert any("checkpoint.json is missing beside results.jsonl" in line for line in reply.standing)


def test_a_saved_record_waiting_for_review_is_counted_and_left_out_of_answers(
    tmp_path: Path,
) -> None:
    held = held_for_review(price())
    directory = saved(tmp_path / "review", entry(held, state=REVIEW_REQUIRED), entry(forecast()))
    reading = read_state_dir(directory)
    assert reading.waiting == 1
    assert len(reading.incidents) == 2
    engine = ChatEngine(reading, chain_factory=no_chain)
    reply = engine.ask(TOMATO_TA)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "wait for a person's review" in reply.missing
    assert any("1 record(s) waiting for a person's review" in line for line in reply.standing)


def test_an_unfinished_saved_record_is_not_read_as_an_answer(tmp_path: Path) -> None:
    directory = saved(
        tmp_path / "failed",
        Entry(
            record_id="AGRI-NONE",
            state=FAILED,
            mode="structured",
            prompt_version=MAPPING_VERSION,
            schema_version="2.0",
            error="the record held no text",
        ),
    )
    reading = read_state_dir(directory)
    assert reading.incidents == [] and reading.records == 0
    assert read_state_dir(directory).unreadable == []


def test_a_saved_reading_that_no_longer_validates_is_reported_not_dropped_silently(
    tmp_path: Path,
) -> None:
    broken = price().to_storage_document()
    broken.pop("incident_id")
    directory = saved(
        tmp_path / "broken",
        Entry(
            record_id=price().provenance.record_id,
            state=COMPLETED,
            mode="structured",
            prompt_version=MAPPING_VERSION,
            schema_version="2.0",
            incident=broken,
        ),
        entry(forecast()),
    )
    reading = read_state_dir(directory)
    assert len(reading.incidents) == 1
    assert len(reading.unreadable) == 1
    assert "incident_id" in reading.unreadable[0]
    engine = ChatEngine(reading, chain_factory=no_chain)
    assert any(
        "saved reading(s) were not usable" in line for line in engine.ask(WEATHER_TA).standing
    )


def value_reply(incidents: Any = None, question: str = TOMATO_TA) -> ChatReply:
    return values_engine(incidents if incidents is not None else [price()]).ask(question)


def test_a_value_reply_that_claims_two_model_calls_is_refused_by_the_model() -> None:
    with pytest.raises(ValidationError, match="no model or one composition"):
        ChatReply.model_validate(value_reply().as_dict() | {"model_calls": 2})


def test_a_value_reply_claiming_a_model_call_without_the_composition_prompt_is_refused() -> None:
    body = value_reply().as_dict() | {
        "model_calls": 1,
        "provider": "groq",
        "model": "gpt-oss-120b",
    }
    with pytest.raises(ValidationError, match="names the composition prompt"):
        ChatReply.model_validate(body)
    with pytest.raises(ValidationError, match="names the provider and model it asked"):
        ChatReply.model_validate(
            body
            | {
                "prompt_version": VALUE_PROMPT_VERSION,
                "prompt_digest": VALUE_PROMPT_DIGEST,
                "model": LOOKUP_MODEL,
            }
        )


def test_a_value_reply_that_names_a_provider_is_refused_by_the_model() -> None:
    with pytest.raises(ValidationError, match="names no provider or model"):
        ChatReply.model_validate(value_reply().as_dict() | {"provider": "groq", "model": "gpt"})


def test_a_value_reply_that_offers_an_explanation_is_refused_by_the_model() -> None:
    body = value_reply().as_dict()
    body["statements"] = [
        body["statements"][0]
        | {"kind": StatementKind.POSSIBLE_EXPLANATION.value, "review": ReviewState.REVIEW_REQUIRED.value}
    ]
    with pytest.raises(ValidationError, match="never an explanation"):
        ChatReply.model_validate(body)


def test_a_value_reply_without_a_citation_is_refused_by_the_model() -> None:
    body = value_reply().as_dict()
    body["statements"] = [body["statements"][0] | {"evidence_ids": []}]
    with pytest.raises(ValidationError, match="no record behind it"):
        ChatReply.model_validate(body)
    body["statements"] = [
        body["statements"][0] | {"kind": StatementKind.CALCULATED.value, "evidence_ids": []}
    ]
    with pytest.raises(ValidationError, match="cites the field it was copied from"):
        ChatReply.model_validate(body)


def test_a_refused_search_claims_no_records_even_though_it_searched_them() -> None:
    engine, _ = search_engine(floods(), question_read=read(), answer_said=never())
    reply = engine.ask(EN_ASK)
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert (reply.records, reply.sources, reply.events) == (0, 0, 0)
    assert reply.record_ids == [] and reply.citations == [] and reply.record_kinds == []
    assert reply.model_calls == 2
    assert any(line.startswith("answered from 3 reading(s)") for line in reply.standing)
    assert any("got no usable answer" in line for line in reply.standing)


def test_a_statement_citing_evidence_the_reply_does_not_carry_is_refused() -> None:
    body = value_reply().as_dict()
    body["statements"] = [body["statements"][0] | {"evidence_ids": ["ev-invented"]}]
    with pytest.raises(ValidationError, match="no quotation for"):
        ChatReply.model_validate(body)


def test_a_reply_counting_records_it_does_not_list_is_refused() -> None:
    body = value_reply().as_dict()
    with pytest.raises(ValidationError, match="is not the"):
        ChatReply.model_validate(body | {"record_ids": body["record_ids"] + ["AGRI-ELSEWHERE"]})
    with pytest.raises(ValidationError, match="does not list"):
        ChatReply.model_validate(
            body | {"citations": [body["citations"][0] | {"record_id": "AGRI-ELSEWHERE"}]}
        )


def test_a_value_reply_that_borrows_a_quotation_from_another_record_is_refused() -> None:
    body = value_reply().as_dict()
    with pytest.raises(ValidationError, match="carries no prompt digest"):
        ChatReply.model_validate(body | {"prompt_digest": "0" * 64})


def test_a_reply_counting_sources_it_does_not_cite_is_refused() -> None:
    body = value_reply().as_dict() | {"sources": 4}
    with pytest.raises(ValidationError, match="publisher id"):
        ChatReply.model_validate(body)


def test_a_refusal_that_still_states_something_is_refused() -> None:
    body = value_reply().as_dict() | {"state": AnswerState.NOT_ANSWERABLE.value, "missing": "nothing here"}
    with pytest.raises(ValidationError, match="states nothing"):
        ChatReply.model_validate(body)


def test_an_answer_that_carries_a_missing_reason_is_refused() -> None:
    body = value_reply().as_dict() | {"missing": "an invented reason"}
    with pytest.raises(ValidationError, match="only a refusal"):
        ChatReply.model_validate(body)


def test_a_reply_for_another_session_is_not_the_id_this_one_derives() -> None:
    body = value_reply().as_dict() | {"session_id": "somebody-elses-session"}
    with pytest.raises(ValidationError, match="reply id is not the one"):
        ChatReply.model_validate(body)


def test_a_source_fact_statement_with_no_evidence_is_refused() -> None:
    with pytest.raises(ValidationError, match="no record behind it"):
        ChatStatement.model_validate(
            {"text": "a price", "kind": StatementKind.SOURCE_FACT.value, "confidence": 1.0}
        )


def test_a_reply_carries_no_field_the_pipeline_never_agreed_to() -> None:
    body = value_reply().as_dict() | {"certainty": 0.99}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ChatReply.model_validate(body)


def test_the_reply_id_is_stable_within_a_session_and_changes_with_the_question() -> None:
    first = value_reply()
    again = value_reply()
    other = value_reply(question=WEATHER_TA)
    assert first.reply_id == again.reply_id == reply_id_for(DEFAULT_SESSION, first.question, None)
    assert other.reply_id != first.reply_id
    assert first.session_id == other.session_id == DEFAULT_SESSION


def test_a_reply_serialises_to_json_that_reads_back_the_same() -> None:
    reply = value_reply()
    assert ChatReply.model_validate(json.loads(json.dumps(reply.as_dict()))) == reply


def test_search_replies_carry_their_evidence_as_citations_with_the_publishers_url() -> None:
    engine, _ = search_engine(floods())
    reply = engine.ask(EN_ASK)
    assert reply.citations
    for item in reply.citations:
        assert item.evidence_id and item.quote
        assert item.record_kind is RecordKind.INCIDENT
        assert item.source_id
    assert {item.record_id for item in reply.citations} <= set(reply.record_ids)


def test_print_reply_keeps_record_values_apart_from_a_models_reading() -> None:
    body = value_reply().as_dict()
    body["statements"] = body["statements"] + [
        {
            "text": "The price may ease if more arrivals are reported",
            "kind": StatementKind.POSSIBLE_EXPLANATION.value,
            "review": ReviewState.REVIEW_REQUIRED.value,
            "confidence": 0.4,
        }
    ]
    reply = ChatReply.model_validate(
        body
        | {
            "path": ChatPath.RECORD_SEARCH.value,
            "model_calls": 2,
            "provider": "groq",
            "model": "groq-compound-beta",
            "prompt_version": ANSWER_PROMPT_VERSION,
        }
    )
    out = io.StringIO()
    print_reply(reply, stream=out)
    printed = out.getvalue()
    assert "From the records:" in printed
    assert "A model's reading of the same records, never a finding:" in printed
    assert printed.index("From the records:") < printed.index("A model's reading")
    assert "(held for a person)" in printed
    assert "model calls: 2" in printed


def test_print_reply_reports_a_refusal_as_not_answered() -> None:
    out = io.StringIO()
    print_reply(values_engine([price()]).ask(PADDY_TA), stream=out)
    printed = out.getvalue()
    assert "Not answered:" in printed
    assert "Answer:" not in printed
    assert "model calls: 0" in printed


def test_print_reply_detail_names_the_kind_of_record_the_answer_came_from() -> None:
    out = io.StringIO()
    print_reply(value_reply(), stream=out, detailed=True)
    printed = out.getvalue()
    assert " context AGRI-MDU-ANNA_NAGAR-TOMATO-2026-10-03 data.commodity: Tomato" in printed
    assert " incident " not in printed


def test_the_eligible_records_are_the_accepted_ones_of_that_kind_newest_first() -> None:
    older = price(event_time="2026-09-01T00:00:00", record_id="AGRI-OLD")
    newer = price(event_time="2026-10-05T00:00:00", record_id="AGRI-NEW")
    kept = eligible_values([older, newer, forecast(), flood("NEWS-A", "ஆகஸ்ட் 2 அன்று", "2026-08-02")],
                           ChatTopic.MARKET)
    assert [item.provenance.record_id for item in kept] == ["AGRI-NEW", "AGRI-OLD"]


def test_an_undated_record_is_listed_last_rather_than_compared_to_a_date_it_never_had() -> None:
    dated = price(record_id="AGRI-DATED", event_time="2026-10-05T00:00:00")
    naive = price(record_id="AGRI-UNAWARE", event_time="2026-10-05T00:00:00+00:00")
    undated = price(record_id="AGRI-UNDATED")
    undated = undated.model_copy(update={"event_time": None})
    kept = eligible_values([dated, naive, undated], ChatTopic.MARKET)
    assert kept[-1].provenance.record_id == "AGRI-UNDATED"
    assert {item.provenance.record_id for item in kept[:2]} == {"AGRI-DATED", "AGRI-UNAWARE"}


def test_matching_values_reports_what_it_narrowed_by_and_what_it_could_not() -> None:
    kept, notes = matching_values([price(), forecast()], "tomato price in Tamil Nadu")
    assert [item.incident_id for item in kept] == [price().incident_id]
    assert notes == [
        "the place the question names: tamil nadu",
        "the field values the question names: commodity tomato",
    ]
    kept, notes = matching_values([], "tomato price")
    assert kept == []
    assert "no record here holds 'tomato'" in notes[0]
    kept, notes = matching_values([price()], "onion price")
    assert kept == []
    assert "no record here holds 'onion'" in notes[0]
    assert "commodity Tomato" in notes[0]


def test_the_place_vocabulary_a_question_is_matched_against_is_the_records_own_wording() -> None:
    stored = floods()[0]
    vocabulary = place_vocabulary([stored])
    assert named_places("Which areas reported flooding in சோழவந்தான் கிராமத்தில்?", vocabulary)
    assert named_places("Which areas reported flooding in Sholavandan?", vocabulary)
    assert named_places("Which areas reported flooding?", vocabulary) == []


def test_the_tamil_wording_the_chat_matches_on_is_written_in_tamil_characters() -> None:
    words = (
        set(VALUE_ALIASES)
        | {synonym for pair in VALUE_ALIASES.values() for synonym in pair}
        | set(RELATIVE_DAYS)
        | set(MONTH_WORDS)
        | set(WEEK_WORDS)
        | set(ELABORATION_WORDS)
    )
    tamil = sorted(item for item in words if any(TAMIL_LOW <= ord(char) <= TAMIL_HIGH for char in item))
    assert tamil, "the wording the chat matches on holds no Tamil character, so it is mojibake"
    for item in tamil:
        assert unicodedata.normalize("NFC", item) == item
        assert script_of(item) is QuestionScript.TAMIL
        assert said_in(item, (item,)) == [fold(item)]


def test_the_place_vocabulary_matches_a_tamil_name_written_in_either_normal_form() -> None:
    vocabulary = place_vocabulary([floods()[0]])
    matched = named_places("Which areas reported flooding in சோழவந்தான் கிராமத்தில்?", vocabulary)
    assert matched
    decomposed = unicodedata.normalize("NFD", " ".join(matched))
    assert decomposed != " ".join(matched)
    assert named_places(decomposed, vocabulary) == matched


def test_a_mixed_script_price_question_is_answered_from_the_records_with_no_model_asked() -> None:
    reply = values_engine([price(), forecast()]).ask("தக்காளி price?")
    assert reply.path is ChatPath.RECORD_VALUES
    assert reply.topic is ChatTopic.MARKET
    assert reply.state is AnswerState.GROUNDED
    assert reply.script == "mixed"
    assert reply.model_calls == 0
    assert "Tomato" in reply.headline
    assert NO_MODEL_NOTE in reply.limitations


def test_a_records_value_words_are_its_own_field_values_plus_the_aliases_that_name_them() -> None:
    index = value_fields([price()])
    assert {"data.commodity", "data.market", "data.unit", "data.min_price"} <= set(index)
    assert index["data.commodity"]["tomato"] == {price().incident_id}
    assert "தக்காளி" in index["data.commodity"]
    assert "46.0" in index["data.min_price"]
    assert not any(field.removeprefix("data.").endswith("_id") for field in index)


def test_a_question_the_records_cannot_answer_is_answered_as_general_background() -> None:
    engine, judge = search_engine(floods(), answer_said=refused, background=background())
    reply = engine.ask("what is a mandi committee")
    assert [request.meta.get("prompt_version") for request in judge.asked] == [
        COPILOT_PROMPT_VERSION,
        ANSWER_PROMPT_VERSION,
        GENERAL_PROMPT_VERSION,
    ]
    assert reply.path is ChatPath.GENERAL
    assert reply.topic is ChatTopic.GENERAL
    assert reply.state is AnswerState.QUALIFIED
    assert reply.missing is None
    assert reply.model_calls == 3
    assert (reply.provider, reply.model) == ("scripted", "gpt-oss-120b")
    assert (reply.prompt_version, reply.prompt_digest) == (
        GENERAL_PROMPT_VERSION,
        GENERAL_PROMPT_DIGEST,
    )
    assert reply.headline == reply.statements[0].text
    assert [item.kind for item in reply.statements] == [StatementKind.POSSIBLE_EXPLANATION] * 3
    assert {item.review for item in reply.statements} == {ReviewState.REVIEW_REQUIRED}
    assert reply.limitations[0] == GENERAL_NOTE
    assert reply.limitations[1].startswith("the records were asked first and did not answer this:")


def test_a_general_answer_cites_no_record_and_claims_no_reading_of_one() -> None:
    engine, _ = search_engine(floods(), answer_said=refused, background=background())
    reply = engine.ask("what is a mandi committee").as_dict()
    assert reply["citations"] == []
    assert reply["record_ids"] == []
    assert reply["record_kinds"] == []
    assert (reply["records"], reply["sources"], reply["events"]) == (0, 0, 0)
    assert all(not item["evidence_ids"] for item in reply["statements"])
    assert reply["understood"] is None


def test_the_general_question_is_asked_with_its_own_prompt_and_the_question_alone() -> None:
    engine, judge = search_engine(floods(), answer_said=refused, background=background())
    engine.ask("what is a mandi committee")
    request = judge.asked[-1]
    assert request.system == GENERAL_RULES
    assert request.json_schema == GENERAL_SCHEMA
    assert request.user == "Question: what is a mandi committee"
    assert request.meta == {"prompt_version": GENERAL_PROMPT_VERSION}
    assert "No record of this district is in front of you" in request.system
    assert "nothing you write here comes from one" in request.system
    assert "a fact of geography, history, science, law, sport or world affairs" in request.system
    assert "no emoji, no decoration, no markup" in request.system


def test_a_general_answer_naming_a_place_the_records_hold_is_dropped() -> None:
    engine, judge = search_engine(
        floods(),
        answer_said=refused,
        background=background(headline="In Sholavandan a mandi sits near the river."),
    )
    reply = engine.ask("what is a mandi committee")
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "it names sholavandan, a place these records hold" in reply.limitations[0]
    assert len(judge.asked) == 3


def test_a_general_answer_giving_a_figure_for_a_value_question_is_dropped() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(headline="Tomato runs 40 rupees a kilo in a good week."),
    )
    reply = engine.ask("what is the mandi tomato price")
    assert reply.topic is ChatTopic.MARKET
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "it carries the number(s) 40 for a question about a value only a record can give" in (
        reply.limitations[0]
    )


def test_a_general_answer_the_model_calls_a_district_claim_is_dropped() -> None:
    engine, _ = search_engine(
        floods(), answer_said=refused, background=background(district_claims=True)
    )
    reply = engine.ask("what is a mandi committee")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "claims something about this district" in reply.limitations[0]


def test_a_general_answer_the_model_declines_says_what_the_records_would_need() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            answerable=False, district_claims=False, headline="", lines=[],
            needs_records="a rainfall figure for the week asked",
        ),
    )
    reply = engine.ask("what is a mandi committee")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.limitations[0].endswith(": a rainfall figure for the week asked")


def test_a_refusal_carries_no_headline_when_the_model_summarised_the_records_it_cited_nothing_from(
) -> None:
    engine, judge = search_engine(
        floods(),
        answer_said=lambda request: {
            **refused(request),
            "headline": "the reports shown here say nothing about a priority",
        },
    )
    reply = engine.ask("what is a mandi committee")
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.headline is None
    assert reply.statements == []
    assert reply.missing
    assert len(judge.asked) == 3


def test_a_refusal_the_records_answered_nothing_on_still_takes_a_general_background_answer(
) -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=lambda request: {
            **refused(request),
            "headline": "the reports shown here say nothing about a priority",
        },
        background=background(),
    )
    reply = engine.ask("what is a mandi committee")
    assert reply.path is ChatPath.GENERAL
    assert reply.headline == "A mandi is a market yard where farm produce is bought and sold."


def test_the_background_prompt_answers_a_question_no_district_record_could_carry() -> None:
    for phrase in (
        "What holds for any reader is not a district claim",
        "a count of administrative divisions, a physical constant, a year or the syntax of a language",
        "give what does hold for any reader",
        "keep its indentation in spaces and write no markdown fence",
    ):
        assert phrase in GENERAL_RULES


def test_an_answer_keeps_what_it_turns_on_apart_from_what_the_run_holds() -> None:
    grounded, _ = search_engine(floods())
    reply = grounded.ask(EN_ASK)
    assert reply.state is AnswerState.GROUNDED
    assert not any(line.startswith("answered from ") for line in reply.limitations)
    assert any(line.startswith("answered from 3 reading(s)") for line in reply.standing)
    assert any(line.startswith("no link decisions were available") for line in reply.standing)
    assert not any(line.startswith("no link decisions were available") for line in reply.limitations)


def test_a_value_answer_reports_its_own_readings_and_holds_the_run_note_apart() -> None:
    reply = values_engine([price(), forecast()]).ask(TOMATO_TA)
    assert reply.limitations[0] == NO_MODEL_NOTE
    assert not any("no link decisions were saved" in line for line in reply.limitations)
    assert any("no link decisions were saved" in line for line in reply.standing)


def test_a_background_answer_carries_the_run_notes_it_was_refused_under() -> None:
    engine, _ = search_engine(floods(), answer_said=refused, background=background())
    reply = engine.ask("what is a mandi committee")
    assert reply.path is ChatPath.GENERAL
    assert reply.limitations[0] == GENERAL_NOTE
    assert any(line.startswith("answered from 3 reading(s)") for line in reply.standing)


def test_a_general_guard_refuses_an_answer_the_model_called_answerable_and_wrote_nothing() -> None:
    draft = GeneralDraft.model_validate(background(headline="", lines=[]))
    assert general_refusal(draft, places=["periyar"], value_shaped=False) == (
        "the model said the question is answerable and then wrote nothing"
    )


def test_a_refusal_that_never_reached_a_model_asks_no_general_question() -> None:
    reply = values_engine([price()]).ask("onion price")
    assert reply.path is ChatPath.RECORD_VALUES
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.model_calls == 0


def test_a_session_out_of_questions_asks_no_general_question() -> None:
    engine, judge = search_engine(
        floods(), answer_said=refused, background=background(), limits=ChatLimits(requests_per_window=1)
    )
    engine.ask("what is a mandi committee")
    assert len(judge.asked) == 3
    refused_again = engine.ask("what is a mandi committee")
    assert refused_again.state is AnswerState.NOT_ANSWERABLE
    assert len(judge.asked) == 3
    assert "nothing was asked of a model for this reply" in refused_again.limitations


def test_a_general_reply_carrying_a_citation_is_refused_by_the_contract() -> None:
    citation = ChatCitation(
        record_id="NEWS-A",
        incident_id="NEWS-A",
        source_id="dinakaran",
        source_type="news",
        field="data.issues[0].kind",
        quote="வெள்ளம்",
        evidence_id="EV-1",
        record_kind=RecordKind.INCIDENT,
    )
    with pytest.raises(ValidationError, match="cites no record, because none was read"):
        general_reply(citations=[citation], record_ids=["NEWS-A"], record_kinds=[RecordKind.INCIDENT],
                      records=1, sources=1)
    with pytest.raises(ValidationError, match="never grounded in the records"):
        general_reply(state=AnswerState.GROUNDED)
    with pytest.raises(ValidationError, match="an explanation, not evidence"):
        general_reply(
            statements=[
                ChatStatement(
                    text="A mandi is a market yard.",
                    kind=StatementKind.CALCULATED,
                    review=ReviewState.ACCEPTED,
                    confidence=1.0,
                )
            ]
        )
    with pytest.raises(ValidationError, match="asked a model and says which one"):
        general_reply(model_calls=0)


def test_a_general_answer_is_printed_as_background_never_as_a_reading_of_records() -> None:
    engine, _ = search_engine(floods(), answer_said=refused, background=background())
    printed = io.StringIO()
    print_reply(engine.ask("what is a mandi committee"), stream=printed)
    text = printed.getvalue()
    assert "General background, from no record:" in text
    assert "A model's reading of the same records" not in text
    assert "From the records:" not in text


def test_the_general_prompt_is_pinned_to_one_digest() -> None:
    assert GENERAL_PROMPT_VERSION == "general-5"
    assert GENERAL_PROMPT_DIGEST == "a3d01bc03fd26e18"


def test_the_route_prompt_is_pinned_to_one_digest() -> None:
    assert ROUTE_PROMPT_VERSION == "route-1"
    assert ROUTE_PROMPT_DIGEST == "1e90272db9278dad"


def test_the_ceiling_is_high_enough_for_a_read_a_full_search_and_a_background_answer() -> None:
    assert MAX_MODEL_CALLS_PER_REQUEST == 5
    assert ChatLimits().max_model_calls == MAX_MODEL_CALLS_PER_REQUEST


def two_publishers() -> list[Incident]:
    items = floods()
    second = items[1].model_copy(
        update={"provenance": items[1].provenance.model_copy(update={"source_id": "hindu"})}
    )
    return [items[0], second, items[2]]


def quoting_one(request: LLMRequest) -> dict[str, Any]:
    per = printed(request)
    first = min(per)
    return {
        "headline": "ஒரு வெள்ளம் பதிவாகியுள்ளது.",
        "statements": [
            statement(COUNTED, "calculated", [first], []),
            statement(FLOODED, "source_fact", [first], [per[first][0]]),
        ],
        "caveats": [],
        "events": [first],
        "evidence": [per[first][0]],
        "saved": [],
        "confidence": 0.9,
    }


def test_an_answer_that_quotes_one_record_names_only_that_record_and_its_publisher() -> None:
    engine, _ = search_engine(two_publishers(), answer_said=quoting_one)
    reply = engine.ask(EN_ASK)
    assert reply.records == len({item.record_id for item in reply.citations}) == 1
    assert reply.record_ids == [reply.citations[0].record_id]
    assert reply.sources == len({item.source_id for item in reply.citations}) == 1
    assert any("the search found 3 record(s) and this answer quotes 1" in line for line in reply.limitations)


def general_engine(**fields: Any) -> tuple[ChatEngine, Answering]:
    fields.setdefault("question_read", read(intent="general", places=[], understood="Not a record question.",
                                            confidence=0.95))
    fields.setdefault("answer_said", never())
    fields.setdefault("background", background())
    fields.setdefault("route", general_route())
    return search_engine(floods(), **fields)


def test_a_greeting_is_read_as_no_record_question_and_asked_of_the_model() -> None:
    engine, judge = general_engine()
    reply = engine.ask("hi how are you")
    assert reply.path is ChatPath.GENERAL
    assert reply.topic is ChatTopic.GENERAL
    assert reply.state is AnswerState.QUALIFIED
    assert reply.model_calls == 2
    assert (reply.provider, reply.model) == ("scripted", "gpt-oss-120b")
    assert reply.citations == [] and reply.record_ids == [] and reply.records == 0
    assert len(judge.asked) == 2
    assert all(item.meta["prompt_version"] != ANSWER_PROMPT_VERSION for item in judge.asked)


def test_a_general_question_costs_one_route_and_one_background_answer() -> None:
    engine, judge = general_engine()
    engine.ask("who is the prime minister of india")
    versions = [item.meta["prompt_version"] for item in judge.asked]
    assert versions == [ROUTE_PROMPT_VERSION, GENERAL_PROMPT_VERSION]


def test_a_general_answer_carries_no_note_about_a_search_that_never_ran() -> None:
    engine, _ = general_engine()
    reply = engine.ask("how many countries are there")
    assert reply.limitations == [GENERAL_NOTE]
    assert reply.understood is None
    assert reply.missing is None


def test_a_general_question_does_not_borrow_the_question_before_it() -> None:
    engine, _ = search_engine(
        floods(),
        question_read=lambda request: (
            read(intent="general", places=[], understood="Not a record question.", confidence=0.95)
            if "how are you" in request.user
            else read()
        ),
        answer_said=said(),
        background=background(),
    )
    first = engine.ask("how many floods happened in மதுரை")
    assert first.path is ChatPath.RECORD_SEARCH
    second = engine.ask("and how are you?")
    assert second.question == "and how are you?"
    assert second.carried_from is None
    assert second.path is ChatPath.GENERAL


def test_a_repeated_general_question_is_refused_once_and_asked_the_model_once_again() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = AnswerStore(Path(tmp))
        store.load()
        first, _ = general_engine(store=store)
        assert first.ask("what is a mandi").model_calls == 2
        again, judge = general_engine(store=store)
        reply = again.ask("what is a mandi")
        assert reply.state is AnswerState.QUALIFIED
        assert reply.model_calls == 1
        assert len(judge.asked) == 1


def test_a_greeting_that_carries_a_question_is_answered_from_the_records() -> None:
    engine = values_engine([price(commodity="Tomato", min_price=42.0, max_price=48.0)])
    reply = engine.ask("hi, what is the tomato price")
    assert reply.path is ChatPath.RECORD_VALUES
    assert any("42" in item.text for item in reply.statements)


def test_a_refusal_that_asked_no_model_says_no_background_question_follows() -> None:
    reply = quiet().ask("what is a mandi committee")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert reply.model_calls == 0
    assert "no background question is asked of a model for a refusal that asked none" in (
        reply.limitations[0]
    )


def test_a_general_answer_is_printed_without_a_searched_line() -> None:
    engine, _ = general_engine()
    printed = io.StringIO()
    print_reply(engine.ask("what is a mandi committee"), stream=printed)
    assert "Searched:" not in printed.getvalue()


def test_a_question_about_the_world_is_answered_as_background_and_keeps_its_figure() -> None:
    engine, judge = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            headline="There are 195 countries in the world.",
            lines=["193 of them hold a seat at the United Nations.", "Two are observer states."],
            confidence=0.95,
        ),
        route=general_route(),
    )
    reply = engine.ask("how many countries are there")
    assert [request.meta.get("prompt_version") for request in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        GENERAL_PROMPT_VERSION,
    ]
    assert reply.path is ChatPath.GENERAL
    assert reply.state is AnswerState.QUALIFIED
    assert reply.model_calls == 2
    assert any("195" in item.text for item in reply.statements)
    assert reply.citations == []
    assert reply.record_ids == []
    assert reply.events == 0


def test_a_background_answer_may_name_the_place_the_question_asked_about() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            headline="Sholavandan is a town that grew up around a market yard.",
            lines=["Its farmers bring paddy there on the days the yard sits."],
        ),
    )
    reply = engine.ask("what is special about sholavandan")
    assert reply.path is ChatPath.GENERAL
    assert "dropped" not in " ".join(reply.limitations)


def test_a_background_answer_still_cannot_carry_in_a_place_the_question_never_mentioned() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            headline="In Sholavandan the rainy season peaked in October.",
            lines=["Farmers there lost standing crop."],
        ),
    )
    reply = engine.ask("how many countries are there")
    assert reply.path is ChatPath.RECORD_SEARCH
    assert "it names sholavandan, a place these records hold" in reply.limitations[0]


def test_a_refusal_that_spent_every_call_but_one_still_asks_its_background_question() -> None:
    engine, judge = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            headline="A named official is a field of the reports themselves.",
            lines=["Background wording never supplies one."],
        ),
        limits=ChatLimits(max_model_calls=4),
    )
    reply = engine.ask("which officials are named in these reports")
    assert reply.model_calls == 4
    assert reply.path is ChatPath.GENERAL
    assert reply.state is AnswerState.QUALIFIED
    assert len(judge.asked) == 4


def test_a_routing_call_is_counted_against_the_calls_one_request_makes() -> None:
    engine, judge = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            headline="A named official is a field of the reports themselves.",
            lines=["Background wording never supplies one."],
        ),
        limits=ChatLimits(max_model_calls=3),
    )
    reply = engine.ask("which officials are named in these reports")
    assert reply.model_calls == 3
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert len(judge.asked) == 3


def test_the_route_request_is_asked_with_its_own_prompt_and_the_conversation_above_it() -> None:
    plain = route_request("how many districts are there?")
    assert plain.system == ROUTE_RULES
    assert plain.json_schema == ROUTE_SCHEMA
    assert plain.user == "Question: how many districts are there?"
    assert plain.meta == {"prompt_version": ROUTE_PROMPT_VERSION}
    lines = route_request(
        "in tamil", history=[("what is the tomato price?", "Tomato is 42 rupees a kilo.")]
    ).user.splitlines()
    assert lines[0] == "Conversation so far, oldest first:"
    assert lines[1] == "Asked: what is the tomato price?"
    assert lines[2] == "Answered: Tomato is 42 rupees a kilo."
    assert lines[-1] == "Question: in tamil"
    for phrase in (
        "When a question could be read either way, set needs_records true",
        "writing, summarising, translating, or code in any language",
    ):
        assert phrase in ROUTE_RULES
    assert history_lines([("", "an answer with no question above it")]) == []
    assert RouteDraft.model_validate({"needs_records": False, "not_a_field": 1}).general is None


def test_a_route_that_left_the_flag_unset_searches_the_records_anyway() -> None:
    engine, judge = search_engine(
        floods(),
        route={"needs_records": None, "general": None, "confidence": None},
        answer_said=said(),
        background=background(),
    )
    reply = engine.ask(EN_ASK)
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.state is AnswerState.GROUNDED
    assert [item.meta["prompt_version"] for item in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        COPILOT_PROMPT_VERSION,
        ANSWER_PROMPT_VERSION,
    ]


def test_a_route_the_provider_never_answered_searches_the_records_and_says_so() -> None:
    def route(request: Any) -> dict[str, Any]:
        raise LLMError("the routing model timed out")

    engine, judge = search_engine(floods(), route=route, answer_said=said(), background=background())
    reply = engine.ask(EN_ASK)
    assert reply.path is ChatPath.RECORD_SEARCH
    assert reply.model_calls == 2
    assert "the routing question asked before the records were searched was not answered" in (
        reply.limitations[0]
    )
    assert "so the records were searched as though it needed them" in reply.limitations[0]
    assert [item.meta["prompt_version"] for item in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        COPILOT_PROMPT_VERSION,
        ANSWER_PROMPT_VERSION,
    ]


def test_a_general_answer_wearing_an_emoji_is_dropped() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(headline="Hi! \U0001F44B How can I help you today?"),
        route=general_route(),
    )
    reply = engine.ask("hi")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert "this office's answers are plain text in any language" in reply.limitations[0]


def test_a_general_answer_longer_than_an_answer_holds_is_dropped() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(lines=[f"Step {number}." for number in range(MAX_GENERAL_LINES)]),
        route=general_route(),
    )
    reply = engine.ask("explain machine learning simply")
    assert reply.state is AnswerState.NOT_ANSWERABLE
    assert (
        f"it wrote {MAX_GENERAL_LINES + 1} lines, more than the {MAX_GENERAL_LINES} an answer here holds"
        in reply.limitations[0]
    )


def test_a_general_answer_can_carry_code_with_its_own_indentation() -> None:
    code = [
        "public class Reverse {",
        "    public static void main(String[] args) {",
        "        String word = args[0];",
        "        System.out.println(new StringBuilder(word).reverse());",
        "    }",
        "}",
    ]
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(headline="A Java program that reverses a string:", lines=code),
        route=general_route(),
    )
    reply = engine.ask("write a simple java program to reverse a string")
    assert reply.path is ChatPath.GENERAL
    assert reply.state is AnswerState.QUALIFIED
    assert [item.text for item in reply.statements[1:]] == code
    assert all("`" not in item.text for item in reply.statements)


def test_a_translation_is_answered_in_the_language_the_question_asked_for() -> None:
    engine, _ = search_engine(
        floods(),
        answer_said=refused,
        background=background(
            headline="தமிழில்: வணக்கம், நான் எப்படி உதவ முடியும்?",
            lines=["இது நேரடி மொழிபெயர்ப்பு."],
        ),
        route=general_route(),
    )
    reply = engine.ask("translate hello how are you into tamil")
    assert reply.path is ChatPath.GENERAL
    assert reply.statements[0].text.startswith("தமிழில்")
    assert reply.script == "latin"


def test_a_repeated_general_question_costs_the_route_and_the_answer_again() -> None:
    engine, judge = general_engine()
    first = engine.ask("how many countries are there")
    again = engine.ask("how many countries are there")
    assert (first.model_calls, again.model_calls) == (2, 2)
    assert [item.meta["prompt_version"] for item in judge.asked] == [
        ROUTE_PROMPT_VERSION,
        GENERAL_PROMPT_VERSION,
        ROUTE_PROMPT_VERSION,
        GENERAL_PROMPT_VERSION,
    ]


def test_one_session_is_never_given_another_session_s_conversation() -> None:
    engine, judge = general_engine()
    engine.ask("how many countries are there", session_id="collector-a")
    engine.ask("and in detail", session_id="collector-a")
    engine.ask("hi", session_id="collector-b")
    asked = [item for item in judge.asked if item.meta["prompt_version"] == GENERAL_PROMPT_VERSION]
    assert "Asked: how many countries are there" in asked[1].user
    assert "Asked: how many countries are there" not in asked[-1].user


def test_the_conversation_handed_to_the_next_question_is_the_last_answers_only() -> None:
    engine, judge = general_engine(limits=ChatLimits(history_turns=2))
    for number in range(4):
        engine.ask(f"explain gravity in number {number}")
    asked = [item for item in judge.asked if item.meta["prompt_version"] == GENERAL_PROMPT_VERSION]
    lines = asked[-1].user.splitlines()
    assert "Asked: explain gravity in number 1" in lines
    assert "Asked: explain gravity in number 2" in lines
    assert "number 0" not in asked[-1].user


def test_an_answer_handed_on_is_cut_to_the_length_one_turn_holds() -> None:
    engine, judge = general_engine(limits=ChatLimits(turn_chars=40))
    engine.ask("explain gravity simply")
    engine.ask("and in detail")
    asked = [item for item in judge.asked if item.meta["prompt_version"] == GENERAL_PROMPT_VERSION]
    line = next(item for item in asked[-1].user.splitlines() if item.startswith("Answered:"))
    assert len(line) <= len("Answered: ") + 40
