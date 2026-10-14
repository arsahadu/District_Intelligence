from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pytest
from pydantic import ValidationError

from intelligence.checkpoint import CheckpointError
from intelligence.contract import Incident, RecordKind, ReviewState
from intelligence.fallback import Chain
from intelligence.intelligence import extract_incident
from intelligence.linking import (
    EVENTS_FILENAME,
    LINKS_FILENAME,
    LINK_PROMPT_DIGEST,
    LINK_RULES,
    EventLink,
    LinkStore,
    Verdict,
    candidate_pairs,
    eligible,
    link_id_for,
    link_incidents,
)
from intelligence.llm import LLMProvider, LLMRequest, LLMResponse, QuotaExhaustedError
from intelligence.pipeline import EXIT_OK, main
from intelligence.tests.builders import CONTENT, listing, quote, responder, water_answer
from intelligence.tests.record_fixtures import base_record, waterlogging

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)

FLOOD_TA = "சோழவந்தான் கிராமத்தில் வைகை ஆற்று வெள்ளம் புகுந்ததால் 40 வீடுகள் நீரில் மூழ்கின"
FLOOD_EN = "Floodwater from the Vaigai entered Sholavandan village and submerged 40 houses"
ACCIDENT_TA = "சோழவந்தான் கிராமத்தில் அரசு பேருந்து லாரி மீது மோதியதில் மூவர் காயமடைந்தனர்"
FLOOD_LATER_TA = "சோழவந்தான் கிராமத்தில் அக்டோபர் 6 அன்று மீண்டும் வெள்ளம் புகுந்து 12 வீடுகள் சேதமடைந்தன"
FLOOD_DATED_TA = "சோழவந்தான் கிராமத்தில் அக்டோபர் 5 அன்று வெள்ளம் புகுந்து 40 வீடுகள் மூழ்கின"
CRASH_TA = "திருமங்கலம் அருகே இரு சக்கர வாகனம் கவிழ்ந்ததில் ஒருவர் காயமடைந்தார்"
DISTRICT_FLOOD_TA = "மாவட்டத்தில் பெய்த மழையால் வெள்ளம் ஏற்பட்டது"


def report(
    record_id: str,
    content: str,
    *,
    kind: str,
    kind_quote: str,
    place: Optional[str] = None,
    place_quote: Optional[str] = None,
    date: Optional[str] = None,
    date_quote: Optional[str] = None,
    language: str = "ta",
    source_id: str = "dinamalar",
    published: str = "2026-10-06T09:00:00",
) -> Incident:
    record = base_record(
        record_id=record_id,
        source_id=source_id,
        title=f"report {record_id}",
        event_time=published,
        data={"content": content, "language": language},
        source_url=f"https://news.example.test/{record_id}",
        raw_reference=f"https://news.example.test/{record_id}",
    )
    answer: dict[str, Any] = {
        "record_kind": "incident",
        "description": quote(content, content),
        "incident_type": quote(content, kind_quote) | {"value": kind},
        "event_status": quote(content, kind_quote) | {"value": "reported"},
    }
    if place:
        answer["locations"] = [
            quote(content, place_quote) | {"text": place, "normalized_name": place,
                                           "location_type": "village", "role": "event_location"}
        ]
    if date:
        answer["event_time"] = quote(content, date_quote) | {"value": date, "precision": "day"}
    return extract_incident(record, provider=Answering(lambda request: answer))


def flood_ta(record_id: str = "NEWS-A", **fields: Any) -> Incident:
    return report(record_id, FLOOD_TA, kind="flood", kind_quote="வெள்ளம் புகுந்ததால்",
                  place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்", **fields)


def flood_en(record_id: str = "NEWS-B", **fields: Any) -> Incident:
    return report(record_id, FLOOD_EN, kind="flood", kind_quote="Floodwater", language="en",
                  source_id="the_hindu", place="Sholavandan", place_quote="Sholavandan village",
                  **fields)


@dataclass
class Answering(LLMProvider):
    answer: Callable[[LLMRequest], Any]
    asked: list = field(default_factory=list)
    name: str = "scripted"

    @property
    def model(self) -> str:
        return "gpt-oss-120b"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.asked.append(request)
        data = self.answer(request)
        return LLMResponse(provider=self.name, model=self.model, text=json.dumps(data), data=data)


def shown_ids(request: LLMRequest, label: str) -> list[str]:
    block = request.user.split("\n\nReport B\n")[0 if label == "A" else 1]
    return re.findall(r"^\[([^\]]+)\]", block, flags=re.M)


def verdict(name: str = "same_event", confidence: Any = 0.92, **overrides: Any) -> Callable:
    def answer(request: LLMRequest) -> dict[str, Any]:
        body = {
            "verdict": name,
            "confidence": confidence,
            "evidence_a": shown_ids(request, "A")[:2] if name == "same_event" else [],
            "evidence_b": shown_ids(request, "B")[:2] if name == "same_event" else [],
            "conflicts": [],
            "explanation": "Both describe the Vaigai flooding 40 houses in Sholavandan.",
        }
        return body | overrides

    return answer


def judged(answer: Callable) -> tuple[Answering, Callable[[], Chain]]:
    judge = Answering(answer)
    return judge, lambda: Chain.single(judge)


def never() -> Chain:
    raise AssertionError("no comparison was needed, so no model may be built")


def test_the_fixtures_are_accepted_incidents_the_linker_can_use():
    for incident in (flood_ta(), flood_en()):
        assert incident.record_kind is RecordKind.INCIDENT
        assert incident.review_required is False
        assert incident.locations[0].review is ReviewState.ACCEPTED


def test_a_tamil_and_an_english_report_of_one_flood_become_one_event_with_both_sources():
    a, b = flood_ta(), flood_en()
    before = [a.model_dump(mode="json"), b.model_dump(mode="json")]
    judge, chain = judged(verdict())

    found = link_incidents([a, b], chain_factory=chain, now=NOW)

    assert found.candidates == 1 and found.asked == 1
    link = found.links[0]
    assert link.verdict is Verdict.SAME_EVENT and link.review is ReviewState.ACCEPTED
    assert link.merges and link.findings == [] and link.conflicts == []
    assert link.shared == ["incident_type flood", "place sholavandan"]
    assert {link.a.source_id, link.b.source_id} == {"dinamalar", "the_hindu"}
    assert link.a.source_url == a.provenance.source_url
    assert link.a.published_at == a.provenance.declared_event_time
    assert link.a.quotes == [a.evidence_by_id()[eid].quote for eid in link.a.evidence_ids]
    assert (link.provider, link.model) == ("scripted", "gpt-oss-120b")
    assert found.distinct_events == 1 and found.undecided == 0
    group = found.groups[0]
    assert group.incident_ids == ["INC-NEWS-A", "INC-NEWS-B"]
    assert group.record_ids == ["NEWS-A", "NEWS-B"]
    assert group.source_urls == sorted([a.provenance.source_url, b.provenance.source_url])
    assert group.link_ids == [link.link_id] and group.review is ReviewState.ACCEPTED
    assert [a.model_dump(mode="json"), b.model_dump(mode="json")] == before
    prompt = judge.asked[0].user
    assert FLOOD_TA in prompt and FLOOD_EN in prompt


def test_two_reports_that_share_only_madurai_are_never_compared():
    a = report("NEWS-A", DISTRICT_FLOOD_TA, kind="flood", kind_quote="வெள்ளம் ஏற்பட்டது")
    b = report("NEWS-B", CRASH_TA, kind="vehicle_accident", kind_quote="இரு சக்கர வாகனம் கவிழ்ந்ததில்")
    assert a.provenance.district_hint == b.provenance.district_hint == "Madurai"

    found = link_incidents([a, b], chain_factory=never, now=NOW)

    assert found.candidates == 0 and found.links == []
    assert found.distinct_events == 2


def test_a_different_happening_in_the_same_village_stays_a_separate_event():
    a = flood_ta()
    b = report("NEWS-B", ACCIDENT_TA, kind="vehicle_accident", kind_quote="லாரி மீது மோதியதில்",
               place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்")
    _, chain = judged(verdict("separate_event", 0.95, conflicts=["a flood vs a bus collision"]))

    found = link_incidents([a, b], chain_factory=chain, now=NOW)

    link = found.links[0]
    assert link.shared == ["place sholavandan"]
    assert "incident_type flood vs vehicle_accident" in link.conflicts
    assert link.verdict is Verdict.SEPARATE_EVENT and link.review is ReviewState.ACCEPTED
    assert not link.merges and found.distinct_events == 2 and found.undecided == 0


def test_the_same_kind_of_event_on_another_day_is_held_for_review_however_sure_the_model_is():
    a = report("NEWS-A", FLOOD_DATED_TA, kind="flood", kind_quote="வெள்ளம் புகுந்து",
               place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்",
               date="2026-10-05T00:00:00", date_quote="அக்டோபர் 5 அன்று")
    b = report("NEWS-B", FLOOD_LATER_TA, kind="flood", kind_quote="மீண்டும் வெள்ளம் புகுந்து",
               place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்",
               date="2026-10-06T00:00:00", date_quote="அக்டோபர் 6 அன்று",
               published="2026-10-07T09:00:00")
    _, chain = judged(verdict("same_event", 0.97))

    found = link_incidents([a, b], chain_factory=chain, now=NOW)

    link = found.links[0]
    assert link.conflicts == ["event_time 2026-10-05 vs 2026-10-06"]
    assert link.review is ReviewState.REVIEW_REQUIRED and not link.merges
    assert any("the pipeline found: event_time" in item for item in link.findings)
    assert found.distinct_events == 2 and found.undecided == 1
    assert found.counts()["Distinct events"] == "at most 2 (1 candidate pair(s) undecided)"


def test_reports_published_weeks_apart_are_not_candidates():
    found = link_incidents(
        [flood_ta(published="2026-09-01T09:00:00"), flood_en(published="2026-10-06T09:00:00")],
        chain_factory=never,
        now=NOW,
    )
    assert found.candidates == 0 and found.distinct_events == 2


@pytest.mark.parametrize(
    ("answer", "finding"),
    [
        (verdict(evidence_a=["ev-invented"]), "report A cites evidence 'ev-invented'"),
        (verdict(confidence=0.6), "confidence 0.60 is below 0.8"),
        (verdict(confidence=1.5), "confidence 1.5; 0.5 was used"),
        (verdict(name="probably"), "verdict 'probably', which is not one of"),
        (verdict(conflicts=["the English report says 40 houses, the Tamil one 12"]),
         "same_event while the model itself listed"),
        (verdict(evidence_b=[]), "same_event needs a quote cited from each report"),
        (verdict(name="undetermined", confidence=0.9), "could not tell"),
    ],
)
def test_an_unsupported_same_event_answer_goes_to_review_instead_of_merging(answer, finding):
    _, chain = judged(answer)

    found = link_incidents([flood_ta(), flood_en()], chain_factory=chain, now=NOW)

    link = found.links[0]
    assert link.review is ReviewState.REVIEW_REQUIRED and not link.merges
    assert any(finding in item for item in link.findings), link.findings
    assert found.distinct_events == 2


def test_context_and_review_required_readings_are_never_linked():
    context = flood_ta("NEWS-C").model_copy(update={"record_kind": RecordKind.CONTEXT})
    held = flood_en().model_copy(deep=True)
    held.validation.state = ReviewState.REVIEW_REQUIRED
    held.validation.issues = []
    assert held.review_required

    kept = eligible([flood_ta(), context, held, flood_ta()])
    assert [incident.incident_id for incident in kept] == ["INC-NEWS-A"]


def test_a_second_run_reuses_every_decision_and_writes_no_duplicate(tmp_path):
    a, b = flood_ta(), flood_en()
    _, chain = judged(verdict())
    first = link_incidents([a, b], chain_factory=chain, store=LinkStore(tmp_path).load(), now=NOW)
    written = (tmp_path / LINKS_FILENAME).read_bytes()

    second = link_incidents([b, a, a], chain_factory=never, store=LinkStore(tmp_path).load())

    assert second.reused == 1 and second.asked == 0
    assert [link.link_id for link in second.links] == [link.link_id for link in first.links]
    assert [group.event_id for group in second.groups] == [group.event_id for group in first.groups]
    assert (tmp_path / LINKS_FILENAME).read_bytes() == written
    assert len(written.decode("utf-8").splitlines()) == 1
    events = json.loads((tmp_path / EVENTS_FILENAME).read_text(encoding="utf-8"))
    assert events["prompt_digest"] == LINK_PROMPT_DIGEST
    assert [item["incident_ids"] for item in events["events"]] == [["INC-NEWS-A", "INC-NEWS-B"]]


def test_a_report_whose_reading_changed_is_compared_again(tmp_path):
    _, chain = judged(verdict())
    link_incidents([flood_ta(), flood_en()], chain_factory=chain, store=LinkStore(tmp_path).load(),
                   now=NOW)
    changed = report("NEWS-B", FLOOD_EN + " overnight", kind="flood", kind_quote="Floodwater",
                     language="en", place="Sholavandan", place_quote="Sholavandan village")
    judge, chain = judged(verdict("separate_event", 0.9))

    found = link_incidents([flood_ta(), changed], chain_factory=chain,
                           store=LinkStore(tmp_path).load(), now=NOW)

    assert found.reused == 0 and len(judge.asked) == 1
    assert found.links[0].verdict is Verdict.SEPARATE_EVENT
    assert len((tmp_path / LINKS_FILENAME).read_text(encoding="utf-8").splitlines()) == 1


def test_an_exhausted_quota_leaves_pairs_uncompared_and_the_count_an_upper_bound():
    def refuse(request: LLMRequest) -> Any:
        raise QuotaExhaustedError("daily tokens used up")

    judge, chain = judged(refuse)
    third = report("NEWS-C", FLOOD_TA, kind="flood", kind_quote="வெள்ளம் புகுந்ததால்",
                   place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்")

    found = link_incidents([flood_ta(), flood_en(), third], chain_factory=chain, now=NOW)

    assert found.candidates == 3 and len(judge.asked) == 1
    assert found.not_compared == 3 and found.links == [] and found.failed == []
    assert "daily tokens used up" in found.stop_reason
    assert found.counts()["Distinct events"] == "at most 3 (3 candidate pair(s) undecided)"


def test_an_answer_that_is_not_a_comparison_is_a_failure_not_a_link():
    _, chain = judged(lambda request: {"verdict": "same_event", "evidence_a": "all of it"})

    found = link_incidents([flood_ta(), flood_en()], chain_factory=chain, now=NOW)

    assert found.links == [] and len(found.failed) == 1
    assert "is not a comparison" in found.failed[0]
    assert found.distinct_events == 2


def test_a_chain_that_contradicts_itself_puts_the_whole_event_up_for_review():
    a, b = flood_ta(), flood_en()
    c = report("NEWS-C", FLOOD_TA + " என்று ஊராட்சி தலைவர் தெரிவித்தார்", kind="flood",
               kind_quote="வெள்ளம் புகுந்ததால்", place="Sholavandan",
               place_quote="சோழவந்தான் கிராமத்தில்")
    split = link_id_for("INC-NEWS-A", "INC-NEWS-C")

    def answer(request: LLMRequest) -> dict[str, Any]:
        if request.meta["link_id"] == split:
            return verdict("separate_event", 0.9)(request)
        return verdict()(request)

    _, chain = judged(answer)
    found = link_incidents([a, b, c], chain_factory=chain, now=NOW)

    assert found.distinct_events == 1
    group = found.groups[0]
    assert group.review is ReviewState.REVIEW_REQUIRED
    assert group.findings and split in group.findings[0]
    assert len(group.link_ids) == 2


def test_an_empty_dataset_builds_no_model_and_writes_an_empty_record(tmp_path):
    found = link_incidents([], chain_factory=never, store=LinkStore(tmp_path).load())

    assert (found.eligible, found.candidates, found.distinct_events) == (0, 0, 0)
    assert (tmp_path / LINKS_FILENAME).read_text(encoding="utf-8") == ""


def test_a_damaged_links_file_stops_the_run_instead_of_being_overwritten(tmp_path):
    (tmp_path / LINKS_FILENAME).write_text('{"link_id": "LNK-x"}\n', encoding="utf-8")

    with pytest.raises(CheckpointError, match="line 1 is not a link this pipeline wrote"):
        LinkStore(tmp_path).load()
    assert (tmp_path / LINKS_FILENAME).read_text(encoding="utf-8") == '{"link_id": "LNK-x"}\n'


def test_an_accepted_merge_without_evidence_from_both_reports_cannot_be_written():
    _, chain = judged(verdict())
    link = link_incidents([flood_ta(), flood_en()], chain_factory=chain, now=NOW).links[0]
    document = link.model_dump(mode="json")
    document["b"] |= {"evidence_ids": [], "quotes": []}

    with pytest.raises(ValidationError, match="cites evidence from both reports"):
        EventLink.model_validate(document)


def test_the_comparison_prompt_is_pinned_and_states_what_never_makes_one_event():
    assert LINK_PROMPT_DIGEST == "b7298b247ef1b2c4"
    for phrase in (
        "Sharing an incident type, a district, a village, a department or a category never makes two "
        "reports one event",
        "a Tamil report and an English report",
        "never answer same_event while a conflict stays unexplained",
        "cite only ids printed in square brackets and never invent one",
        "if you are guessing, answer undetermined",
    ):
        assert phrase in LINK_RULES


def test_candidates_stay_bounded_per_incident():
    pool = [
        report(f"NEWS-{index:02d}", FLOOD_TA, kind="flood", kind_quote="வெள்ளம் புகுந்ததால்",
               place="Sholavandan", place_quote="சோழவந்தான் கிராமத்தில்")
        for index in range(12)
    ]
    pairs = candidate_pairs(pool, per_incident=2)
    assert len(pairs) <= 12 * 2 < 66
    assert all(pair.a.incident_id < pair.b.incident_id for pair in pairs)


def test_the_pipeline_links_two_reports_of_one_event_and_reuses_the_link_next_run(tmp_path, capsys):
    records = [
        waterlogging(),
        waterlogging(record_id="NEWS-MDU-WLR-0002",
                     source_url="https://www.dinamalar.com/news/madurai/rain/4338981"),
    ]

    def answer(request: LLMRequest) -> dict[str, Any]:
        if "link_id" in request.meta:
            return verdict()(request)
        return water_answer()

    provider = Answering(answer)
    argv = ["--all", "--link-events", "--state-dir", str(tmp_path)]

    code = main(argv, provider=provider, environ={}, transport=responder(listing(*records)))

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "Event linking (link-1" in out
    assert "Distinct events: 1" in out
    assert "INC-NEWS-MDU-WLR-0001 <-> INC-NEWS-MDU-WLR-0002: same_event 0.92 accepted" in out
    assert len([r for r in provider.asked if "link_id" in r.meta]) == 1

    provider.asked.clear()
    code = main(argv, provider=provider, environ={}, transport=responder(listing(*records)))

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert provider.asked == []
    assert "Reused decisions: 1" in out and "Distinct events: 1" in out
    assert len((tmp_path / LINKS_FILENAME).read_text(encoding="utf-8").splitlines()) == 1
