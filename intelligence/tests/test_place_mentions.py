"""Stage 6 places: raw surfaces the record printed, each pinned to the span that printed it."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from intelligence.extraction.places import PlaceExtraction
from intelligence.extraction.place_expressions import (
    BLOCKED_OBLIQUE,
    GENERIC_UNLOCATED,
    PLURAL_GENERIC,
)
from intelligence.mapping.assembly import map_record
from intelligence.mapping.enrichment import enrich_incident
from intelligence.models.enums import GranularityLevel, MentionRole, MentionType
from intelligence.models.enums import ResolutionState, ReviewReason
from intelligence.tests import record_fixtures as F

EVENT_ROLES = (MentionRole.EVENT_LOCATION, MentionRole.EVENT_CONTAINER)
GEOGRAPHY_KEYS = {
    "latitude",
    "longitude",
    "lat",
    "lng",
    "canonical_place_id",
    "geocoder_version",
    "gazetteer_id",
    "polygon",
    "coordinates",
}


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


def places_of(payload) -> PlaceExtraction:
    return enrich_incident(map_record(payload)).places


def surfaces(extraction: PlaceExtraction) -> list[str]:
    return [mention.surface for mention in extraction.mentions]


def refused(extraction: PlaceExtraction, reason: str) -> list[str]:
    return [rejection.surface for rejection in extraction.rejections if rejection.reason == reason]


def walk_keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in walk_keys(value)}
    if isinstance(node, list):
        return {key for item in node for key in walk_keys(item)}
    return set()


@pytest.fixture(scope="module")
def article() -> PlaceExtraction:
    return places_of(F.tamil_article())


@pytest.fixture(scope="module")
def closure() -> PlaceExtraction:
    return places_of(F.temple_closure())


@pytest.fixture(scope="module")
def outage() -> PlaceExtraction:
    return places_of(F.power_outage())


@pytest.fixture(scope="module")
def listing() -> PlaceExtraction:
    return places_of(F.events_listing())


@pytest.fixture(scope="module")
def english() -> PlaceExtraction:
    return places_of(F.english_article())


def test_a_tamil_city_named_in_the_text_becomes_a_mention(article: PlaceExtraction):
    madurai = [mention for mention in article.mentions if mention.surface == "மதுரை"]

    assert madurai, surfaces(article)
    assert {mention.expression.entry for mention in madurai} == {"மதுரை"}
    assert all(mention.mention_type is MentionType.DISTRICT for mention in madurai)


def test_the_event_place_is_the_district_the_headline_named(article: PlaceExtraction):
    events = [mention for mention in article.mentions if mention.role in EVENT_ROLES]

    assert [mention.role for mention in events] == [MentionRole.EVENT_CONTAINER]
    assert events[0].surface == "மதுரை"
    assert article.best_event_location_mention_id == events[0].mention_id


def test_a_dateline_is_where_the_report_was_filed_not_where_the_event_was(
    article: PlaceExtraction,
):
    dateline = next(
        mention
        for mention in article.mentions
        if mention.role is MentionRole.REPORTING_ORIGIN
    )

    assert dateline.surface == "மதுரை"
    assert dateline.field == "data.content"
    assert article.district_text is None or article.district_text.mention_id != dateline.mention_id


def test_every_place_the_article_prints_is_kept_as_its_own_mention(article: PlaceExtraction):
    for surface in ("ராமேஸ்வரம்", "சென்னை", "கோவை", "எழும்பூர்", "மானாமதுரை"):
        assert surface in surfaces(article), surface


def test_a_type_word_with_no_name_in_front_of_it_is_refused(outage: PlaceExtraction):
    assert "நகர்" in refused(outage, GENERIC_UNLOCATED)
    assert "ரோடு" in refused(outage, GENERIC_UNLOCATED)
    assert "காலனி" in refused(outage, GENERIC_UNLOCATED)


def test_the_same_type_word_with_a_name_in_front_of_it_is_a_place(outage: PlaceExtraction):
    assert "ஸ்ரீராம் நகர்" in surfaces(outage)
    assert "ஜெய் நகர்" in surfaces(outage)


def test_a_plural_of_a_kind_word_names_no_single_place(outage: PlaceExtraction):
    assert "தெருக்கள்" in refused(outage, PLURAL_GENERIC)
    assert "பகுதிகள்" in refused(outage, PLURAL_GENERIC)


def test_an_oblique_case_on_a_word_that_is_not_a_place_is_refused(article: PlaceExtraction):
    assert "ரயில்" in refused(article, BLOCKED_OBLIQUE)
    assert "விரைவில்" in refused(article, BLOCKED_OBLIQUE)
    assert "பயணத்தில்" in refused(article, BLOCKED_OBLIQUE)


def test_a_locative_surface_is_stored_exactly_as_it_was_printed(closure: PlaceExtraction):
    mention = next(m for m in closure.mentions if m.role in EVENT_ROLES)

    assert mention.surface == "மதுரை மீனாட்சி அம்மன் கோயிலில்"
    assert mention.text_normalized == "மதுரை மீனாட்சி அம்மன் கோயிலில்"
    assert mention.mention_type is MentionType.INSTITUTION


def test_a_type_head_takes_the_type_and_granularity_of_the_head_it_ends_with(
    listing: PlaceExtraction,
):
    temple = next(m for m in listing.mentions if m.surface == "பெருமாள் கோயில்")

    assert temple.mention_type is MentionType.INSTITUTION
    assert temple.granularity is GranularityLevel.LANDMARK


def test_an_uncertain_place_type_stays_uncertain_rather_than_being_guessed(
    article: PlaceExtraction,
):
    northern = next(m for m in article.mentions if m.surface == "வடமாநில")

    assert northern.mention_type is MentionType.UNKNOWN
    assert northern.granularity is GranularityLevel.STATE


def test_an_english_street_is_read_from_the_latin_words_it_is_spelled_with(
    english: PlaceExtraction,
):
    street = next(m for m in english.mentions if m.role is MentionRole.EVENT_LOCATION)

    assert street.surface == "East Madurai main road"
    assert street.mention_type is MentionType.STREET
    assert street.granularity is GranularityLevel.STREET


@pytest.mark.parametrize("surface", ["Madurai", "MADURAI"])
def test_a_tanglish_spelling_reaches_the_row_that_lists_it(surface: str):
    extraction = places_of(record("Complaint filed in " + surface + " on Sunday.", title=""))

    mention = extraction.mentions[0]
    assert mention.surface == surface
    assert mention.expression.entry == "மதுரை"
    assert mention.expression.rule == "latin-spelling"
    assert mention.expression.via == "latin"
    assert mention.mention_type is MentionType.DISTRICT


def test_a_latin_type_word_widens_the_surface_it_heads():
    extraction = places_of(record("Complaint filed in Madurai district on Sunday.", title=""))

    mention = extraction.mentions[0]
    assert mention.surface == "Madurai district"
    assert mention.mention_type is MentionType.DISTRICT
    assert mention.expression.kind == "type_head"


def test_a_title_mention_and_a_content_mention_keep_their_own_fields(closure: PlaceExtraction):
    fields = {(mention.field, mention.surface) for mention in closure.mentions}

    assert ("title", "மதுரை மீனாட்சி கோயிலில்") in fields
    assert ("data.content", "மதுரை மீனாட்சி அம்மன் கோயிலில்") in fields


def test_mentions_are_ordered_by_the_field_read_then_the_offset_they_were_cut_at(
    article: PlaceExtraction,
):
    order = [(mention.field, mention.char_start) for mention in article.mentions]

    assert order == sorted(order, key=lambda item: (item[0] != "title", item[0], item[1]))


def test_one_name_printed_nine_times_is_one_mention_with_nine_spans(article: PlaceExtraction):
    madurai = next(m for m in article.mentions if m.role is MentionRole.EVENT_CONTAINER)

    assert len(madurai.occurrences) == 9
    assert len(set(madurai.evidence_ids)) == len(madurai.evidence_ids)


def test_every_span_reproduces_the_words_it_quotes_from_the_untouched_field(article: PlaceExtraction):
    draft = enrich_incident(map_record(F.tamil_article()))
    text = {path: source.text for path, source in draft.fields.items()}

    for mention in article.mentions:
        for occurrence in mention.occurrences:
            evidence = occurrence.evidence
            cut = text[evidence.field][evidence.char_start : evidence.char_end]
            assert cut == evidence.quote
            assert cut == occurrence.expression.surface
        assert mention.surface in text[mention.field]


def test_no_place_mention_is_built_without_citing_where_it_was_printed(
    article: PlaceExtraction,
):
    for mention in article.mentions:
        assert mention.evidence_ids, mention.describe()
        assert mention.confidence is not None
        assert "lexicon entry" in mention.evidence[0].notes


def test_mention_ids_are_unique_and_carried_the_record_that_printed_them(
    article: PlaceExtraction,
):
    ids = [mention.mention_id for mention in article.mentions]

    assert len(ids) == len(set(ids))
    assert all(id.startswith("LOC-NEWS-MDU-0001-") for id in ids)


def test_two_records_of_identical_text_produce_disjoint_mentions_and_evidence():
    first = places_of(F.tamil_article())
    twin = places_of(F.twin_article())

    assert surfaces(first) == surfaces(twin)
    assert not {mention.mention_id for mention in first.mentions} & {
        mention.mention_id for mention in twin.mentions
    }
    assert not {e.evidence_id for e in first.evidence()} & {
        e.evidence_id for e in twin.evidence()
    }


def test_extraction_is_identical_on_a_second_run():
    first = places_of(F.temple_closure())
    second = places_of(F.temple_closure())

    assert [mention.as_dict() for mention in first.mentions] == [
        mention.as_dict() for mention in second.mentions
    ]
    assert first.best_event_location_mention_id == second.best_event_location_mention_id


def test_a_district_the_feed_configured_is_not_a_place_the_text_stated(outage: PlaceExtraction):
    assert "மதுரை" not in surfaces(outage)
    assert "Madurai" not in surfaces(outage)

    draft = enrich_incident(map_record(F.power_outage()))
    assert draft.incident.spatial.district_hint == "Madurai"
    assert draft.incident.spatial.district_hint_from_text is None


def test_a_district_the_article_itself_prints_is_earned_from_the_text(listing: PlaceExtraction):
    assert "மதுரை மாவட்டம்" in surfaces(listing)

    draft = enrich_incident(map_record(F.events_listing()))
    hint = draft.incident.spatial
    assert hint.district_hint_authority.value == "text_evidence"
    assert hint.district_hint_from_text == "மதுரை மாவட்டம்"
    assert ReviewReason.AMBIGUOUS_LOCATION not in draft.incident.review.reasons


def test_at_most_one_mention_plays_the_event_role_in_a_record():
    for payload in F.capture():
        extraction = places_of(payload)
        events = [mention for mention in extraction.mentions if mention.role in EVENT_ROLES]
        assert len(events) <= 1, extraction.describe()


def test_a_record_that_states_no_event_place_leaves_the_slot_empty(outage: PlaceExtraction):
    assert outage.best_event_location_mention_id is None
    assert [mention.role for mention in outage.mentions] == [MentionRole.MENTIONED_ONLY] * len(
        outage.mentions
    )

    draft = enrich_incident(map_record(F.power_outage()))
    assert ReviewReason.NO_EVENT_LOCATION_CANDIDATE in draft.incident.review.reasons


def test_nothing_out_of_a_metadata_field_becomes_a_place():
    extraction = places_of(
        record(
            "புகார் வந்தது.",
            title="புகார்",
            district="Madurai",
            source_url="https://madurai.example.com/news/1",
            severity="Orange Alert",
            status="UPDATED",
            data={
                "content": "புகார் வந்தது.",
                "language": "ta",
                "station_id": "Madurai-RS",
                "place": "Madurai",
            },
        )
    )

    assert extraction.mentions == ()
    assert "Madurai" not in [rejection.surface for rejection in extraction.rejections]


def test_a_body_of_punctuation_alone_raises_nothing():
    extraction = places_of(record("...   ", title="..."))

    assert extraction.mentions == ()
    assert extraction.rejections == ()
    assert extraction.record_id == "SYNTH-0001"


def test_no_mention_holds_a_coordinate_or_a_canonical_geography_id():
    for payload in F.capture():
        draft = enrich_incident(map_record(payload))
        document = draft.incident.to_storage_document()

        assert not walk_keys(document) & GEOGRAPHY_KEYS
        assert draft.incident.spatial.gis is None
        assert all(
            mention.gazetteer_matched is False for mention in draft.incident.spatial.mentions
        )
        assert draft.incident.spatial.resolution_state is not ResolutionState.RESOLVED


def test_extraction_leaves_the_resolution_state_stage_5_set():
    before = map_record(F.tamil_article())
    after = enrich_incident(before)

    assert before.incident.spatial.resolution_state is ResolutionState.PENDING_GIS
    assert after.incident.spatial.resolution_state is ResolutionState.PENDING_GIS
