"""Stage 7 vocabulary and cue reading: what a printed word may claim, and what it is quoted as."""

from __future__ import annotations

import pytest

from intelligence.config import event_type_cues as vocabulary
from intelligence.config.vocabularies import EVENT_TYPE_FAMILY, departments_for
from intelligence.extraction import boilerplate, cues
from intelligence.extraction.cues import RULE_INFLECTED, RULE_LATIN, RULE_PHRASE, RULE_PRINTED
from intelligence.extraction.mention_text import build_prose, fold
from intelligence.extraction.event_classification import (
    RESOLVE_FLOOR,
    STRUCTURED_WEIGHT,
    TIE_MARGIN,
    TypeScore,
    anchor_of,
    decide_event_type,
    department_hints,
    score_types,
)
from intelligence.extraction.spans import SourceField, verify_evidence
from intelligence.models.actors import Actor
from intelligence.models.classification import RelevanceInfo
from intelligence.models.enums import (
    ActorRole,
    ActorType,
    Department,
    DistrictHintAuthority,
    EventType,
    ExtractionMethod,
    MentionRole,
    RelevanceState,
)
from intelligence.models.spatial import LocationMention, SpatialHint

OUTAGE_LINE = "மதுரையில் மின்தடை தொடர்ந்தது."
STREETLIGHT_LINE = "மதுரை: அண்ணா நகரில் தெருவிளக்கு வேலை செய்யவில்லை என்று கூறினர்."
HOSPITAL_LINE = "மதுரை மருத்துவமனையில் நோயாளிகள் காத்திருந்தனர்."
BAIT_LINE = "இந்த சம்பவம் ஒரு நிகழ்வு. The incident was booked as a case."
AD_LINE = "விளம்பரம் கொடுக்க தொடர்பு கொள்ளவும். Advertisement charges apply."
PORATTAM_LINE = "Porattam made the road unusable for an hour."
PORATTAM_INFLECTED = "மக்கள் போராட்டத்தில் ஈடுபட்டனர்."
NO_EVENT_LINE = "மதுரை மாநகராட்சி அலுவலகம்."


def source_and_prose(
    text: str, *, field: str = "data.content", record_id: str = "SYNTH-0001"
):
    source = SourceField(
        record_id=record_id,
        source_id="dinamalar",
        source_type="news",
        field=field,
        text=text,
        source_url="https://www.dinamalar.com/district/291",
        raw_reference="https://www.dinamalar.com/district/291",
    )
    split = boilerplate.split(source, lowercase=True)
    return source, build_prose(source, split.body, split=split)


def read(text: str, **field_options):
    source, prose = source_and_prose(text, **field_options)
    return source, cues.find_cues(source, prose)


def one_field(finding, field: str = "data.content") -> cues.CueExtraction:
    return cues.CueExtraction(
        record_id="SYNTH-0001", findings=[cues.CueFinding(field=field, matches=list(finding.matches))]
    )


def types_in(finding) -> set[EventType]:
    return {match.event_type for match in finding.matches}


def test_the_cue_vocabulary_has_no_drift():
    errors = vocabulary.cue_lexicon_integrity_errors()

    assert errors == [], "cue vocabulary drift:\n" + "\n".join(errors)


def test_every_cue_names_a_type_the_taxonomy_carries():
    for cue in vocabulary.EVENT_TYPE_CUES:
        assert cue.event_type in EVENT_TYPE_FAMILY, cue
        assert departments_for(cue.event_type), cue
        assert cue.strength in vocabulary.STRENGTHS, cue
        assert cue.note, cue
        assert cue.surface == cue.surface.strip() and cue.surface, cue


def test_a_type_this_vocabulary_is_silent_about_can_never_fire():
    cued = {cue.event_type for cue in vocabulary.EVENT_TYPE_CUES}

    assert cued & vocabulary.UNCUED_EVENT_TYPES == set()
    assert EventType.UNRESOLVED not in cued and EventType.OTHER not in cued


def test_no_word_claims_two_event_types():
    assert set(cues.CUE_LEXICON.contested) == set()


def test_the_refusals_stay_out_of_the_cues():
    generic = {fold(word) for word in vocabulary.NON_CUE_WORDS}
    markers = {fold(row.surface) for row in vocabulary.NON_EVENT_CUES}
    surfaces = {fold(cue.surface) for cue in vocabulary.EVENT_TYPE_CUES}

    assert not surfaces & generic
    assert not surfaces & markers


def test_a_body_name_that_could_answer_to_two_offices_is_refused():
    mapped = set(vocabulary.ACTOR_DEPARTMENT_HINTS)
    ambiguous = set(vocabulary.AMBIGUOUS_ACTOR_TYPES)

    assert not mapped & ambiguous
    assert ActorType.LOCAL_BODY in ambiguous
    assert vocabulary.ACTOR_DEPARTMENT_HINTS[ActorType.HOSPITAL] is Department.PUBLIC_HEALTH


def test_generic_event_words_are_refused_rather_than_believed():
    _, finding = read(BAIT_LINE)

    assert finding.matches == ()
    refused = {refusal.surface for refusal in finding.refusals}
    assert {"சம்பவம்", "நிகழ்வு", "incident", "case"} == refused


def test_a_printed_cue_quotes_the_exact_words_it_read():
    source, finding = read(OUTAGE_LINE)

    assert types_in(finding) == {EventType.POWER_SUPPLY_DISRUPTION}
    match = finding.matches[0]
    assert match.surface == "மின்தடை"
    assert match.rule == RULE_PRINTED
    assert match.decisive is True
    assert source.text[match.char_start : match.char_end] == match.surface
    assert verify_evidence(match.evidence, source.text).validation.value == "validated"
    assert match.evidence.method is ExtractionMethod.DICTIONARY
    assert match.evidence.confidence == cues.QUALITY[RULE_PRINTED]


def test_the_longest_cue_claims_the_words_inside_it():
    source, finding = read(STREETLIGHT_LINE)

    assert [match.surface for match in finding.matches] == ["தெருவிளக்கு வேலை செய்யவில்லை"]
    match = finding.matches[0]
    assert match.rule == RULE_PHRASE
    assert match.event_type is EventType.STREETLIGHT_FAILURE
    assert source.text[match.char_start : match.char_end] == match.surface


def test_an_institution_name_alone_is_never_a_verdict():
    _, finding = read(HOSPITAL_LINE)

    assert finding.matches
    assert all(not match.decisive for match in finding.matches)
    assert types_in(finding) == {EventType.HOSPITAL_SERVICE_GAP}


def test_latin_spelling_and_inflection_are_recorded_as_different_readings():
    _, latin = read(PORATTAM_LINE)
    _, inflected = read(PORATTAM_INFLECTED)

    assert latin.matches[0].rule == RULE_LATIN
    assert latin.matches[0].event_type is EventType.PROTEST_OR_STRIKE
    assert inflected.matches[0].rule == RULE_INFLECTED
    assert inflected.matches[0].surface == "போராட்டத்தில்"
    assert cues.QUALITY[RULE_PRINTED] > cues.QUALITY[RULE_LATIN] > cues.QUALITY[RULE_INFLECTED]


def test_paid_space_is_recorded_as_a_non_event_marker():
    source, finding = read(AD_LINE)

    assert finding.matches == ()
    assert {marker.surface for marker in finding.non_events} == {"விளம்பரம்", "Advertisement"}
    assert all(marker.reason for marker in finding.non_events)
    assert verify_evidence(finding.non_events[0].evidence, source.text).validation.value == "validated"


def test_a_field_with_no_event_in_it_yields_nothing():
    _, finding = read(NO_EVENT_LINE)

    assert (finding.matches, finding.non_events, finding.refusals) == ((), (), ())


def test_extraction_needs_something_to_read():
    with pytest.raises(ValueError):
        cues.extract([])


def test_extraction_reads_every_field_it_is_handed():
    title, title_prose = source_and_prose(OUTAGE_LINE, field="title", record_id="SYNTH-0002")
    body, body_prose = source_and_prose(
        STREETLIGHT_LINE, field="data.content", record_id="SYNTH-0002"
    )

    found = cues.extract([(title, title_prose), (body, body_prose)])

    assert found.record_id == "SYNTH-0002"
    assert [finding.field for finding in found.findings] == ["title", "data.content"]
    assert {match.event_type for match in found.matches} == {
        EventType.POWER_SUPPLY_DISRUPTION,
        EventType.STREETLIGHT_FAILURE,
    }
    assert len(found.evidence()) == 2


def test_a_refusal_is_said_out_loud_once():
    source, finding = read(BAIT_LINE + " " + BAIT_LINE, record_id="SYNTH-0003")
    found = cues.CueExtraction(
        record_id="SYNTH-0003",
        findings=[cues.CueFinding(field=source.field, refusals=finding.refusals)],
    )
    warnings = found.warnings()

    assert warnings
    assert len(warnings) == len(set(warnings))
    assert all("refused" in warning for warning in warnings)


def test_a_record_kind_states_candidates_that_never_reach_the_floor_alone():
    assert STRUCTURED_WEIGHT < RESOLVE_FLOOR
    assert vocabulary.candidates_for("market_price") == (EventType.MARKET_PRICE_DISTRESS,)
    assert vocabulary.candidates_for("article") == ()
    assert vocabulary.is_observation_feed("agriculture", "market_price")
    assert not vocabulary.is_observation_feed("news", "article")


def hinted_district(**fields) -> SpatialHint:
    return SpatialHint(
        district_hint="Madurai",
        district_hint_authority=DistrictHintAuthority.SOURCE_CONFIGURATION,
        **fields,
    )


def scored(event_type: EventType, score: float) -> TypeScore:
    return TypeScore(
        event_type=event_type,
        score=score,
        sources=("test",),
        evidence_ids=("ev",),
        has_primary_cue=True,
    )


def relevant() -> RelevanceInfo:
    return RelevanceInfo(
        state=RelevanceState.INCIDENT,
        is_incident=True,
        reason="test",
        method=ExtractionMethod.DICTIONARY,
        confidence=0.9,
        evidence_ids=["ev"],
    )


def test_a_type_needs_the_floor_and_the_margin_over_the_runner_up():
    floor = decide_event_type(scores=[scored(EventType.FLOOD, RESOLVE_FLOOR - 0.01)], relevance=relevant())
    tie = decide_event_type(
        scores=[scored(EventType.FLOOD, 0.76), scored(EventType.POWER_SUPPLY_DISRUPTION, 0.71)],
        relevance=relevant(),
    )
    clear = decide_event_type(
        scores=[scored(EventType.FLOOD, 0.76), scored(EventType.POWER_SUPPLY_DISRUPTION, 0.60)],
        relevance=relevant(),
    )
    exactly_a_margin_apart = decide_event_type(
        scores=[
            scored(EventType.FLOOD, 0.76),
            scored(EventType.POWER_SUPPLY_DISRUPTION, round(0.76 - TIE_MARGIN, 4)),
        ],
        relevance=relevant(),
    )

    assert floor.event_type is EventType.UNRESOLVED
    assert list(floor.category_scores) == ["flood"]
    assert floor.category_scores["flood"] == pytest.approx(0.54)
    assert [item.value for item in tie.secondary_event_types] == ["flood", "power_supply_disruption"]
    assert tie.event_type is EventType.UNRESOLVED
    assert clear.event_type is EventType.FLOOD
    assert clear.confidence == 0.76
    assert clear.family == EVENT_TYPE_FAMILY[EventType.FLOOD]
    assert clear.evidence_ids == ["ev"]
    assert exactly_a_margin_apart.event_type is EventType.FLOOD


def test_a_record_that_states_no_event_is_not_given_a_type():
    not_event = RelevanceInfo(
        state=RelevanceState.NOT_INCIDENT,
        is_incident=False,
        reason="paid space",
        method=ExtractionMethod.RULE,
        confidence=0.85,
        evidence_ids=["ev"],
    )
    decided = decide_event_type(scores=[scored(EventType.FLOOD, 0.76)], relevance=not_event)
    empty = decide_event_type(scores=[], relevance=relevant())

    assert decided.event_type is EventType.UNRESOLVED
    assert decided.category_scores == {}
    assert "no event type was asserted" in decided.notes
    assert "no event cue" in empty.notes


def test_scoring_keeps_every_candidate_and_the_evidence_that_argued_for_it():
    _, finding = read(OUTAGE_LINE + " " + STREETLIGHT_LINE, record_id="SYNTH-0004")
    scores = score_types(one_field(finding), "article")

    assert [item.event_type for item in scores] == [
        EventType.POWER_SUPPLY_DISRUPTION,
        EventType.STREETLIGHT_FAILURE,
    ]
    assert all(item.evidence_ids for item in scores)
    assert all(item.score >= RESOLVE_FLOOR for item in scores)


def test_the_same_word_twice_corroborates_nothing():
    _, once = read(OUTAGE_LINE, field="title", record_id="SYNTH-0005")
    _, twice = read(OUTAGE_LINE + " " + OUTAGE_LINE, field="data.content", record_id="SYNTH-0005")
    repeated = cues.CueExtraction(
        record_id="SYNTH-0005",
        findings=[
            cues.CueFinding(field="title", matches=list(twice.matches)),
            cues.CueFinding(field="data.content", matches=list(twice.matches)),
        ],
    )

    assert score_types(one_field(once, "title"), "article")[0].score == pytest.approx(0.76)
    assert score_types(repeated, "article")[0].score == pytest.approx(0.76)


def test_two_different_words_for_one_type_corroborate_it():
    _, finding = read(OUTAGE_LINE + " The power outage lasted four hours.", record_id="SYNTH-0006")
    scores = score_types(one_field(finding, "data.content"), "article")

    assert scores[0].event_type is EventType.POWER_SUPPLY_DISRUPTION
    assert scores[0].score == pytest.approx(0.81)
    assert len(scores[0].sources) == 2


def test_a_place_the_text_names_is_not_yet_a_district_this_platform_covers():
    mention = LocationMention(
        mention_id="LOC-1",
        text="Sivaganga",
        role=MentionRole.MENTIONED_ONLY,
        evidence_ids=["ev-mention"],
    )
    unanchored = anchor_of(SpatialHint(mentions=[mention]))
    hinted = anchor_of(hinted_district(mentions=[mention]), ["ev-district"])
    contested = anchor_of(hinted_district(competing_districts=["Sivaganga"]), ["ev-district"])
    agreed = anchor_of(
        hinted_district(
            district_hint_from_text="மதுரை",
            mentions=[
                LocationMention(
                    mention_id="LOC-2",
                    text="மதுரை",
                    role=MentionRole.EVENT_CONTAINER,
                    evidence_ids=["ev-mention"],
                )
            ],
        ),
        ["ev-district"],
    )

    assert unanchored.present is False
    assert hinted.present and hinted.from_text is False and hinted.contested is False
    assert contested.contested is True
    assert agreed.contested is False
    assert agreed.evidence_ids == ("ev-district", "ev-mention")


def test_a_party_named_in_the_text_hints_the_office_that_answers_for_it():
    hospital = Actor(
        actor_id="ACT-1",
        name_text="Madurai Government Hospital",
        actor_type=ActorType.HOSPITAL,
        role=ActorRole.AFFECTED_PARTY,
        method=ExtractionMethod.DICTIONARY,
        evidence_ids=["ev-actor"],
        confidence=0.9,
    )
    nodal = department_hints(EventType.FLOOD, 0.76)
    with_actor = department_hints(EventType.DISEASE_OUTBREAK, 0.76, [hospital])
    agreeing = department_hints(EventType.HOSPITAL_SERVICE_GAP, 0.76, [hospital])

    assert [hint.department for hint in nodal] == [Department.DISTRICT_DISASTER_MANAGEMENT]
    assert nodal[0].basis.value == "taxonomy"
    assert with_actor[-1].department is Department.PUBLIC_HEALTH
    assert with_actor[-1].evidence_ids == ["ev-actor"]
    merged = [hint for hint in agreeing if hint.department is Department.PUBLIC_HEALTH]
    assert len(merged) == 1 and merged[0].basis.value == "both"
