"""Stage 6 actors: only the parties a sentence ties to what happened, each citing that sentence."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from intelligence.extraction.actors import UNCUED as UNCUED_REASON
from intelligence.extraction.actors import ActorExtraction, find_entities
from intelligence.extraction.spans import verify_evidence
from intelligence.config import mention_words as vocabulary
from intelligence.mapping.assembly import map_record
from intelligence.mapping.enrichment import enrich_incident, read_prose
from intelligence.models.enums import ActorRole, ActorType, ExtractionMethod, ScriptType
from intelligence.models.enums import SpanValidation as SV
from intelligence.tests import record_fixtures as F


def record(content: str, *, title: str = "", district: Optional[str] = None, **fields: Any):
    return F.base_record(
        record_id=fields.pop("record_id", "SYNTH-0001"),
        title=title,
        event_time=None,
        location={
            "raw_text": district,
            "district": district,
            "state": "Tamil Nadu" if district else None,
        },
        data={"content": content, "language": "ta", **fields.pop("data", {})},
        **fields,
    )


def enrich(payload):
    return enrich_incident(map_record(payload))


def actors_of(payload) -> ActorExtraction:
    return enrich(payload).actors


def by_surface(extraction: ActorExtraction, surface: str):
    matches = [actor for actor in extraction.actors if actor.surface == surface]
    assert len(matches) == 1, [actor.describe() for actor in extraction.actors]
    return matches[0]


def refused_surfaces(extraction: ActorExtraction) -> list[str]:
    return [refusal.surface for refusal in extraction.refusals]


CORPORATION = actors_of(record("மதுரை மாநகராட்சி புதிய குழாய்களை வழங்கியது."))
DEPARTMENT = actors_of(record("போக்குவரத்து துறை பணிகளை சரிசெய்தது."))
COLLECTOR = actors_of(record("கலெக்டர் ராஜேந்திரன் பார்வையிட்டார்."))
DEVOTEES = actors_of(record("பக்தர்கள் போராட்டம் நடத்தினர்."))
UNCREDITED = actors_of(record("விவசாயிகள் நீர் கட்டுப்பாடு செய்தனர்."))
PRECEDENCE = actors_of(record("போலீசார் கூறினர். போலீசார் ஒழுங்கை நடவடிக்கை எடுத்தனர்."))
SCHOOL = actors_of(record("பள்ளி அருகே கடை உள்ளது."))
ENGLISH = actors_of(record("The traffic department restored power. residents demanded answers."))

ARTICLE = enrich(F.tamil_article())
TWIN = enrich(F.twin_article())
CLOSURE = enrich(F.temple_closure())
LISTING = enrich(F.events_listing())
EN_ARTICLE = enrich(F.english_article())

EXTRACTIONS = (
    ARTICLE.actors,
    TWIN.actors,
    CLOSURE.actors,
    LISTING.actors,
    EN_ARTICLE.actors,
    CORPORATION,
    DEPARTMENT,
    COLLECTOR,
    DEVOTEES,
    UNCREDITED,
    PRECEDENCE,
    SCHOOL,
    ENGLISH,
)


def test_a_body_the_text_makes_the_actor_of_an_action_is_an_actor():
    corporation = by_surface(CORPORATION, "மதுரை மாநகராட்சி")

    assert corporation.actor_type is ActorType.LOCAL_BODY
    assert corporation.role is ActorRole.RESPONDING_AUTHORITY
    assert corporation.official_title is None
    assert corporation.transliterated_latin == "Madurai Corporation"
    assert corporation.entity.modifiers == ("மதுரை",)
    assert corporation.confidence == 0.85


def test_a_department_is_a_body_not_a_person():
    department = by_surface(DEPARTMENT, "துறை")

    assert department.entity.entry == "துறை"
    assert department.actor_type is ActorType.GOVERNMENT_BODY
    assert department.role is ActorRole.RESPONDING_AUTHORITY
    assert department.official_title is None
    assert department.transliterated_latin == "Department"


def test_an_official_keeps_the_title_and_the_name_the_text_printed_next_to_it():
    collector = by_surface(COLLECTOR, "கலெக்டர் ராஜேந்திரன்")

    assert collector.actor_type is ActorType.GOVERNMENT_OFFICIAL
    assert collector.official_title == "கலெக்டர்"
    assert collector.entity.name == "ராஜேந்திரன்"
    assert collector.is_named is True
    assert collector.transliterated_latin == "Collector raajeenthirann"
    assert collector.title_transliterated == "Collector"
    assert collector.confidence == 0.9


def test_a_honourific_only_becomes_a_name_when_the_text_writes_one_after_it():
    from intelligence.extraction.actors import find_entities as find

    prose = read_prose(enrich(record("அமைச்சர் கூறினார்.", title="")), "data.content")
    actor = next(iter(find(prose)))

    assert actor.surface == "அமைச்சர்"
    assert actor.title == "அமைச்சர்"
    assert actor.name is None


def test_a_group_of_people_is_an_actor_when_its_own_sentence_demands_something():
    devotees = by_surface(DEVOTEES, "பக்தர்கள்")

    assert devotees.actor_type is ActorType.COMMUNITY_GROUP
    assert devotees.role is ActorRole.PETITIONER_OR_COMPLAINANT
    assert devotees.entity.entry == "பக்தர்"
    assert devotees.entity.rule == "strip-suffix"
    assert devotees.entity.label == "nominative plural"
    assert devotees.confidence == 0.75


def test_a_record_that_prints_several_parties_lists_several_actors(closure=CLOSURE.actors):
    assert [actor.surface for actor in closure.actors] == [
        "பக்தர்கள்",
        "போலீசாரும்",
        "கோயில் நிர்வாகத்தினரும்",
        "போலீஸ் கமிஷனர் ராஜேந்திரன்",
    ]
    assert [actor.role for actor in closure.actors] == [
        ActorRole.PETITIONER_OR_COMPLAINANT,
        ActorRole.RESPONDING_AUTHORITY,
        ActorRole.UNKNOWN,
        ActorRole.RESPONDING_AUTHORITY,
    ]
    assert [actor.actor_id for actor in closure.actors] == [
        f"ACT-NEWS-MDU-0004-{position}" for position in (1, 2, 3, 4)
    ]


@pytest.mark.parametrize(
    "payload",
    [F.tamil_article(), F.twin_article(), F.temple_closure(), F.english_article()],
)
def test_every_actor_cites_the_phrase_it_was_read_from_and_the_cue_that_made_it_an_actor(payload):
    draft = enrich(payload)
    text = {path: source.text for path, source in draft.fields.items()}

    for actor in draft.actors.actors:
        assert len(actor.evidence_ids) >= 2, actor.describe()
        assert actor.cue in [o.cue for o in actor.occurrences]
        for occurrence in actor.occurrences:
            field = occurrence.entity.field
            phrase = occurrence.phrase_evidence
            assert verify_evidence(phrase, text[field]).validation is SV.VALIDATED
            assert text[field][phrase.char_start : phrase.char_end] == occurrence.entity.surface
            assert f"{occurrence.entity.kind} surface" in phrase.notes
            cue = occurrence.cue
            if cue is None:
                continue
            assert text[field][cue.span.char_start : cue.span.char_end] == cue.text
            assert verify_evidence(cue.evidence, text[field]).validation is SV.VALIDATED
            assert f"{cue.kind} cue" in cue.evidence.notes


def test_an_actor_is_placed_at_the_offsets_the_untouched_field_printed_it():
    commissioner = by_surface(CLOSURE.actors, "போலீஸ் கமிஷனர் ராஜேந்திரன்")
    content = CLOSURE.fields["data.content"].text

    assert commissioner.field == "data.content"
    assert (commissioner.char_start, commissioner.char_end) == (1722, 1748)
    assert content[1722:1748] == "போலீஸ் கமிஷனர் ராஜேந்திரன்"
    assert content[1722 : 1722 + len("போலீஸ் கமிஷனர்")] == commissioner.official_title


def test_the_cue_span_is_the_sentence_own_words_at_its_own_offsets():
    petitioner = by_surface(CLOSURE.actors, "பக்தர்கள்")
    printed = CLOSURE.fields[petitioner.field].text
    cue = petitioner.cue

    assert cue.kind == vocabulary.CUE_DEMAND
    assert printed[cue.span.char_start : cue.span.char_end] == cue.text
    assert cue.span.char_start > petitioner.char_end or cue.span.char_end < petitioner.char_start


def test_one_actor_printed_repeatedly_stays_one_actor_holding_every_printing():
    travellers = by_surface(ARTICLE.actors, "பயணிகளும்")

    assert len(travellers.occurrences) == 3
    assert len(travellers.evidence_ids) == 5
    assert len(set(travellers.evidence_ids)) == 5
    assert [o.entity.field for o in travellers.occurrences] == ["data.content"] * 3
    assert len({(o.entity.char_start, o.entity.char_end) for o in travellers.occurrences}) == 3


def test_a_repeated_stamp_repeats_the_evidence_behind_an_actor_without_collapsing_it():
    devotees = by_surface(CLOSURE.actors, "பக்தர்கள்")

    assert len(devotees.occurrences) == 8
    assert len({o.entity.char_start for o in devotees.occurrences}) == 8


def test_two_records_of_identical_text_keep_their_actors_apart():
    first, twin = ARTICLE.actors, TWIN.actors

    assert [a.surface for a in first.actors] == [a.surface for a in twin.actors]
    assert not {a.actor_id for a in first.actors} & {a.actor_id for a in twin.actors}
    assert not {e.evidence_id for e in first.evidence()} & {
        e.evidence_id for e in twin.evidence()
    }


def test_an_entity_the_text_never_ties_to_an_event_is_refused_rather_than_filmed_as_an_actor():
    assert SCHOOL.actors == ()
    assert refused_surfaces(SCHOOL) == ["பள்ளி"]
    refusal = SCHOOL.refusals[0]
    assert refusal.reason == UNCUED_REASON
    assert refusal.entry == "பள்ளி"
    assert "nothing in the sentence" in refusal.note

    surfaces = [actor.surface for actor in ARTICLE.actors.actors]
    assert "மீனவர்களுக்கு" not in surfaces
    assert "மீனவர்களுக்கு" in refused_surfaces(ARTICLE.actors)


def test_a_cue_only_credits_an_actor_whose_type_can_play_the_role_it_implies():
    gates = {
        vocabulary.CUE_RESPONSE: vocabulary.AUTHORITY_ACTOR_TYPES,
        vocabulary.CUE_DEMAND: vocabulary.DEMAND_ACTOR_TYPES,
    }
    seen: set[tuple[str, bool]] = set()

    for extraction in EXTRACTIONS:
        for actor in extraction.actors:
            cue = actor.cue
            credited = gates.get(cue.kind) is None or actor.actor_type in gates[cue.kind]
            assert cue.credited is credited, actor.describe()
            assert actor.role is (vocabulary.CUE_ROLES[cue.kind] if credited else ActorRole.UNKNOWN)
            assert (actor.confidence is None) is (not credited), actor.describe()
            seen.add((cue.kind, credited))

    assert ("response", False) in seen, "the gate has to have been exercised"
    assert {
        ("speech", True),
        ("response", True),
        ("demand", True),
        ("harm", True),
    } <= seen


def test_a_word_that_only_describes_a_group_is_not_credited_with_beating_the_body_roles():
    farmers = by_surface(UNCREDITED, "விவசாயிகள்")

    assert farmers.role is ActorRole.UNKNOWN
    assert farmers.confidence is None
    assert farmers.cue.credited is False
    assert farmers.cue.role is ActorRole.UNKNOWN
    assert farmers.evidence_ids, "an uncertain role still has to show its text"
    assert "cannot play that role" in farmers.notes


def test_the_stronger_of_two_sentences_wins_the_role():
    police = by_surface(PRECEDENCE, "போலீசார்")

    assert len(police.occurrences) == 2
    assert police.role is ActorRole.RESPONDING_AUTHORITY
    assert police.cue.kind == vocabulary.CUE_RESPONSE
    assert vocabulary.ROLE_PRECEDENCE.index(police.role) < vocabulary.ROLE_PRECEDENCE.index(
        ActorRole.REPORTED_BY
    )


def test_a_voice_the_article_quotes_is_reported_by_not_an_actor_on_the_event():
    members = by_surface(ARTICLE.actors, "அமைப்பினர்")

    assert members.role is ActorRole.REPORTED_BY
    assert members.cue.kind == vocabulary.CUE_SPEECH
    assert members.actor_type is ActorType.OTHER
    assert members.confidence == 0.8


def test_an_affiliation_only_names_a_place_the_same_record_printed():
    corporation = by_surface(EN_ARTICLE.actors, "Madurai Corporation")
    places = {mention.mention_id: mention for mention in EN_ARTICLE.places.mentions}

    assert corporation.affiliation_mention_id in places
    assert corporation.affiliation_surface == "Madurai"
    assert corporation.is_named is True
    assert corporation.confidence == 0.85

    incident = EN_ARTICLE.incident
    assert corporation.affiliation_mention_id in {m.mention_id for m in incident.spatial.mentions}


def test_a_modifier_that_could_stand_for_two_places_is_not_guessed():
    from intelligence.extraction.actors import _link

    entity = by_surface(EN_ARTICLE.actors, "Madurai Corporation").entity
    twin = EN_ARTICLE.places.mentions

    assert _link(entity, twin)[0] == "LOC-NEWS-MDU-EN-0001-1"
    assert _link(entity, ()) == (None, None)
    assert _link(entity, [m for m in twin if m.surface != "Madurai"]) == (None, None)


def test_no_actor_is_read_out_of_a_field_that_holds_metadata_not_prose():
    payload = record(
        "புகார் வந்தது.",
        title="புகார்",
        source_url="https://madurai-corporation.example.com/Madurai/news/1",
        severity="Madurai Corporation Orange Alert",
        status="UPDATED by the Collector",
        data={
            "station_id": "Madurai Corporation",
            "place": "Madurai",
            "language": "en",
            "author": "Commissioner Rajendran",
        },
    )

    draft = enrich(payload)
    assert draft.actors.actors == ()
    assert draft.incident.actors == []
    assert "Madurai" not in [m.surface for m in draft.places.mentions]


def test_an_actor_report_is_a_transliteration_of_the_printed_form_never_a_replacement():
    commissioner = by_surface(CLOSURE.actors, "போலீஸ் கமிஷனர் ராஜேந்திரன்")

    assert commissioner.name_text == "போலீஸ் கமிஷனர் ராஜேந்திரன்"
    assert commissioner.transliterated_latin == "Police Commissioner raajeenthirann"
    assert commissioner.script is ScriptType.TAMIL
    assert commissioner.language_code == "ta"

    engineer = by_surface(EN_ARTICLE.actors, "Executive Engineer K. Rajendran")
    assert engineer.script is ScriptType.LATIN
    assert engineer.official_title == "Executive Engineer"
    assert engineer.entity.name == "K. Rajendran"
    assert engineer.role is ActorRole.REPORTED_BY


def test_the_language_a_record_declares_is_not_the_language_an_actor_was_written_in():
    draft = enrich(record("The traffic department restored power.", data={"language": "ta"}))
    actor = draft.actors.actors[0]

    assert actor.script is ScriptType.LATIN
    assert actor.language_code == "und"
    assert draft.incident.language.inherited_language_hint == "ta"
    assert draft.incident.language.primary_language == "en"
    assert draft.incident.language.detection is not None


def test_the_models_the_incident_carries_are_the_actors_the_extraction_kept():
    for draft in (ARTICLE, CLOSURE, EN_ARTICLE, LISTING):
        built = draft.actors.model_actors()
        assert [a.model_dump() for a in draft.incident.actors] == [a.model_dump() for a in built]
        ledger = {e.evidence_id for e in draft.incident.evidence}
        for actor in draft.incident.actors:
            assert actor.evidence_ids, actor.actor_id
            assert set(actor.evidence_ids) <= ledger
            assert actor.method is ExtractionMethod.DICTIONARY
            if actor.role is ActorRole.UNKNOWN:
                assert actor.confidence is None
            else:
                assert actor.confidence is not None


def test_actor_ids_are_unique_and_belong_to_the_record_that_printed_them():
    for payload in F.capture():
        extraction = actors_of(payload)
        ids = [actor.actor_id for actor in extraction.actors]

        assert len(ids) == len(set(ids))
        assert all(id.startswith(f"ACT-{payload['record_id']}-") for id in ids)


def test_actor_extraction_is_identical_on_a_second_run():
    first, twin = actors_of(F.temple_closure()), actors_of(F.temple_closure())

    assert [a.as_dict() for a in first.actors] == [a.as_dict() for a in twin.actors]
    assert [r.as_dict() for r in first.refusals] == [r.as_dict() for r in twin.refusals]


def test_a_record_with_no_prose_offers_no_actors():
    extraction = actors_of(record("... !!! ...", title=""))

    assert extraction.actors == ()
    assert extraction.refusals == ()
    assert extraction.warnings() == ()


def test_a_title_and_a_body_printing_the_same_party_are_one_actor_with_both_fields():
    extraction = actors_of(record("பக்தர்கள் போராட்டம் நடத்தினர்.", title="பக்தர்கள் முற்றுகை"))

    actor = by_surface(extraction, "பக்தர்கள்")
    assert len(actor.occurrences) == 2
    assert {o.entity.field for o in actor.occurrences} == {"title", "data.content"}
    assert actor.evidence_ids == tuple(dict.fromkeys(actor.evidence_ids))


def test_entity_search_reads_only_the_actor_lexicon():
    payload = record("மதுரை மாநகராட்சி பக்தர்கள்", title="")
    entities = find_entities(read_prose(enrich(payload), "data.content"))

    assert [entity.surface for entity in entities] == ["மதுரை மாநகராட்சி", "பக்தர்கள்"]
    assert [entity.actor_type for entity in entities] == [ActorType.LOCAL_BODY, ActorType.COMMUNITY_GROUP]
    assert [entity.modifiers for entity in entities] == [("மதுரை",), ()]
