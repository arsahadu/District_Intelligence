"""Stage 8 through the seam it is installed at: classified drafts in, a cluster and a reason out."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from intelligence.extraction import correlation
from intelligence.extraction.correlation import IncidentProfile
from intelligence.mapping import classify_incident, correlate_incidents, enrich_incident, feed_policies, map_record
from intelligence.mapping.assembly import MappingPolicy
from intelligence.mapping.deduplication import profile_of
from intelligence.models.enums import DedupDecision, EventType, RelevanceState, TimePrecision, TimeSemantics
from intelligence.tests import record_fixtures as F

SAME_PAGE = F.tamil_article()
SAME_PAGE_TWICE = F.twin_article()
TEMPLE = F.temple_closure()
OUTAGE = F.power_outage()
TOMATO = F.market_price(commodity="Tomato")
ONION = F.market_price(commodity="Onion", min_price=28.0, max_price=32.0)
TOMATO_AGAIN = F.market_price(record_id="AGRI-TOMATO-REPLAY", commodity="Tomato")
SELLUR_TOMATO = F.market_price(market="Sellur", market_id="126", commodity="Tomato")
DISTRESS = F.crop_distress_report()
RAIN = F.weather_warning()

REWORD = F.CLOSURE_CONTENT.replace("பக்தர்கள்", "வழ்பவர்கள்", 1) + " நிர்வாகம் விளக்கம் அளிக்கவில்லை."
SECOND_PAPER = F.temple_closure(
    record_id="THA-MDU-0001",
    source_id="daily_thanthi",
    title="மதுரை மீனாட்சி கோயிலில் கதவு அடைப்பு: தெற்கு கோபுரம் வாசலில் போராட்டம்",
    source_url="https://thanthi.io/madurai/temple-locking-99112",
    raw_reference="https://thanthi.io/madurai/temple-locking-99112",
    data={"content": REWORD, "language": "ta"},
)

MORNING = datetime(2026, 10, 3, 6, 0)
BEFORE_NOON = datetime(2026, 10, 3, 11, 0)
EVENING = datetime(2026, 10, 3, 18, 0)
WEEK_LATER = datetime(2026, 10, 9, 18, 0)

PLACE = frozenset({"meenakshi temple"})
ACTORS = frozenset({"temple board"})

# Half the words in common, and nothing else: the band where a pair is related but not one event.
SHARED_EIGHT = " ".join(f"shared{i:02d}" for i in range(8))
TOLD_A = SHARED_EIGHT + " " + " ".join(f"alpha{i:02d}" for i in range(4))
TOLD_B = SHARED_EIGHT + " " + " ".join(f"beta{i:02d}" for i in range(4))

SHARED_TWELVE = " ".join(f"shared{i:02d}" for i in range(12))
FIRST_SIDE = SHARED_TWELVE + " " + " ".join(f"first{i:02d}" for i in range(8))
SECOND_SIDE = SHARED_TWELVE + " " + " ".join(f"second{i:02d}" for i in range(8))
DISJOINT_A = " ".join(f"alpha{i:02d}" for i in range(12))
DISJOINT_B = " ".join(f"beta{i:02d}" for i in range(12))


def policy_for(payload: dict) -> MappingPolicy:
    return feed_policies.policy_for(payload["source_type"])


def classify(payload: dict):
    policy = policy_for(payload)
    return classify_incident(enrich_incident(map_record(payload, policy), policy), policy)


def correlate(*payloads: dict) -> Any:
    return correlate_incidents([classify(payload) for payload in payloads])


def only_pair(result) -> Any:
    assert len(result.report.relationships) == 1, "the pair was never compared, or more than one was"
    return result.report.relationships[0]


def profile(content: str, **fields: Any) -> IncidentProfile:
    """A hand-built profile: some score shapes no fixture prints on its own."""
    tokens = correlation.tokenize(content)
    record_id = fields.pop("record_id", "SYNTH-A")
    built: dict[str, Any] = {
        "incident_id": "INC-" + record_id,
        "record_id": record_id,
        "source_id": "synth_feed",
        "source_type": "news",
        "record_type": "article",
        "fingerprint": correlation.text_fingerprint(content),
        "token_signature": correlation.token_signature(tokens),
        "record_signature": correlation.text_fingerprint(record_id + content),
        "title_tokens": frozenset(),
        "body_tokens": tokens,
        "event_type": EventType.PROTEST_OR_STRIKE,
        "relevance_state": RelevanceState.INCIDENT,
        "time_semantics": TimeSemantics.PUBLICATION_TIME,
        "event_precision": TimePrecision.MINUTE,
        "district": "Madurai",
        "reported_at": MORNING,
    }
    built.update(fields)
    return IncidentProfile(**built)


def test_a_page_captured_twice_through_its_anchor_is_one_duplicate():
    result = correlate(SAME_PAGE, SAME_PAGE_TWICE)
    cluster = result.report.clusters[0]

    assert only_pair(result).decision is DedupDecision.DUPLICATE
    assert len(result.report.clusters) == 1
    assert cluster.canonical_incident_id == "INC-NEWS-MDU-0001"
    assert result.by_incident["INC-NEWS-MDU-0002"].incident.dedup.duplicate_of == "INC-NEWS-MDU-0001"
    assert result.by_incident["INC-NEWS-MDU-0001"].incident.dedup.merged_from == ["INC-NEWS-MDU-0002"]


def test_the_duplicate_keeps_both_source_urls_for_the_view_source_link():
    result = correlate(SAME_PAGE, SAME_PAGE_TWICE)
    cluster = result.report.clusters[0]

    assert cluster.source_urls == [SAME_PAGE["source_url"], SAME_PAGE_TWICE["source_url"]]
    assert dict(result.sources_for("INC-NEWS-MDU-0002")) == {
        "NEWS-MDU-0001": SAME_PAGE["source_url"],
        "NEWS-MDU-0002": SAME_PAGE_TWICE["source_url"],
    }
    for draft in result.drafts:
        assert all(entry.source_url for entry in draft.incident.evidence)


def separated(result, pairs_read: int = 1) -> None:
    """Nothing clustered, no verdict worth publishing - yet the pair was still shortlisted and read."""
    assert result.report.clusters == []
    assert result.report.relationships == []
    assert result.report.counts["pair_unique"] == pairs_read
    assert {value for value in result.report.decisions.values()} == {DedupDecision.UNIQUE}


def test_two_commodities_at_one_market_on_one_day_stay_separate():
    result = correlate(TOMATO, ONION)

    assert correlation.page_of(TOMATO["source_url"]) == correlation.page_of(ONION["source_url"])
    separated(result)


def test_one_commodity_at_two_markets_is_not_gathered_by_its_headline():
    result = correlate(TOMATO, SELLUR_TOMATO)

    assert not [key for key in result.report.counts if key.startswith("pair_")]
    assert result.report.clusters == [] and result.report.relationships == []
    assert {v for v in result.report.decisions.values()} == {DedupDecision.UNIQUE}


def test_the_same_price_row_reissued_under_a_second_record_id_is_a_duplicate():
    result = correlate(TOMATO, TOMATO_AGAIN)

    assert only_pair(result).decision is DedupDecision.DUPLICATE
    assert result.report.clusters[0].record_ids == [
        "AGRI-MDU-ANNA_NAGAR-TOMATO-2026-10-03",
        "AGRI-TOMATO-REPLAY",
    ]


def test_a_second_paper_that_reworded_the_story_is_a_merge_candidate_not_a_duplicate():
    result = correlate(TEMPLE, SECOND_PAPER)
    relationship = only_pair(result)

    assert relationship.decision is DedupDecision.MERGE_CANDIDATE
    assert relationship.cross_source is True
    assert relationship.blockers == []
    assert result.report.clusters[0].event_types == [EventType.PROTEST_OR_STRIKE]


def test_a_daily_page_that_reprints_the_same_words_each_day_is_not_one_incident():
    day_one = F.weather_warning()
    day_two = F.weather_warning(
        record_id="WEATHER-43360-04-warning",
        title="Madurai Weather Warning - 04-Oct",
        event_time="2026-10-04T00:00:00",
    )
    relationship = correlation.compare_profiles(profile_of(classify(day_one)), profile_of(classify(day_two)))

    assert correlation.page_of(day_one["source_url"]) == correlation.page_of(day_two["source_url"])
    assert relationship.decision is DedupDecision.UNIQUE
    assert correlation.RESTATE_BLOCKER in relationship.blockers
    separated(correlate(day_one, day_two))


def test_an_observation_feed_never_merges_into_another_feed():
    result = correlate(DISTRESS, RAIN, TOMATO)

    assert result.report.clusters == []
    assert {v for v in result.report.decisions.values()}.isdisjoint(
        {DedupDecision.DUPLICATE, DedupDecision.MERGE_CANDIDATE}
    )


def test_the_same_incident_id_supplied_twice_is_refused():
    with pytest.raises(ValueError, match="correlate a set"):
        correlate_incidents([classify(TEMPLE), classify(TEMPLE)])


def test_district_event_type_event_time_and_actors_alone_cannot_clear_a_merge_floor():
    a = profile(
        DISJOINT_A,
        place_surfaces=PLACE,
        actor_names=ACTORS,
        time_semantics=TimeSemantics.EVENT_TIME,
        event_instant=EVENING,
        reported_at=None,
    )
    b = profile(
        DISJOINT_B,
        record_id="SYNTH-B",
        source_id="other_feed",
        place_surfaces=PLACE,
        actor_names=ACTORS,
        time_semantics=TimeSemantics.EVENT_TIME,
        event_instant=EVENING,
        reported_at=None,
    )
    signals = correlation.read_pair(a, b).signals

    assert (signals.content, signals.title) == (0.0, 0.0)
    assert (signals.time, signals.place, signals.actor) == (1.0, 0.9, 1.0)
    assert signals.composite == pytest.approx(0.29)
    assert correlation.compare_profiles(a, b).decision is DedupDecision.UNIQUE


def test_a_story_that_only_shares_its_wording_is_linked_but_not_merged():
    relationship = correlation.compare_profiles(
        profile(TOLD_A, time_semantics=TimeSemantics.EVENT_TIME, event_instant=MORNING),
        profile(
            TOLD_B,
            record_id="SYNTH-B",
            source_id="other_feed",
            time_semantics=TimeSemantics.EVENT_TIME,
            event_instant=BEFORE_NOON,
        ),
    )

    assert relationship.decision is DedupDecision.LINKED
    assert correlation.LINK_COMPOSITE <= relationship.similarity < correlation.UNCERTAIN_COMPOSITE
    assert relationship.confidence is not None and relationship.confidence < 0.7


def test_an_uncertain_pair_records_no_confidence_and_merges_nothing():
    relationship = correlation.compare_profiles(
        profile(
            TOLD_A,
            place_surfaces=PLACE,
            actor_names=ACTORS,
            time_semantics=TimeSemantics.EVENT_TIME,
            event_instant=EVENING,
        ),
        profile(
            TOLD_B,
            record_id="SYNTH-B",
            source_id="other_feed",
            place_surfaces=PLACE,
            actor_names=ACTORS,
            time_semantics=TimeSemantics.EVENT_TIME,
            event_instant=EVENING,
        ),
    )

    assert relationship.decision is DedupDecision.UNRESOLVED
    assert relationship.confidence is None
    assert relationship.similarity >= correlation.UNCERTAIN_COMPOSITE
    assert relationship.similarity < correlation.MERGE_COMPOSITE


def test_a_publication_stamp_days_apart_never_separates_two_accounts():
    stamped = correlation.compare_profiles(
        profile(FIRST_SIDE, event_instant=MORNING, reported_at=MORNING),
        profile(
            FIRST_SIDE + " extra",
            record_id="SYNTH-B",
            source_id="other_feed",
            event_instant=WEEK_LATER,
            reported_at=WEEK_LATER,
        ),
    )
    stated = correlation.compare_profiles(
        profile(FIRST_SIDE, time_semantics=TimeSemantics.EVENT_TIME, event_instant=MORNING),
        profile(
            FIRST_SIDE + " extra",
            record_id="SYNTH-B",
            source_id="other_feed",
            time_semantics=TimeSemantics.EVENT_TIME,
            event_instant=WEEK_LATER,
        ),
    )

    assert correlation.TIME_BLOCKER not in stamped.blockers
    assert stamped.decision is not DedupDecision.UNIQUE
    assert correlation.TIME_BLOCKER in stated.blockers
    assert stated.decision is not DedupDecision.MERGE_CANDIDATE


def test_different_districts_separate_unless_the_text_is_verbatim_the_same():
    apart = correlation.compare_profiles(
        profile(FIRST_SIDE, district="Madurai"),
        profile(SECOND_SIDE, record_id="SYNTH-B", source_id="other_feed", district="Sivagangai"),
    )
    verbatim = correlation.compare_profiles(
        profile(FIRST_SIDE, district="Madurai"),
        profile(FIRST_SIDE, record_id="SYNTH-B", source_id="other_feed", district="Sivagangai"),
    )

    assert apart.decision is DedupDecision.UNIQUE
    assert verbatim.decision is DedupDecision.DUPLICATE
    assert correlation.DISTRICT_BLOCKER in verbatim.blockers


def test_no_incident_is_rewritten_and_no_evidence_is_lost():
    payloads = (SAME_PAGE, SAME_PAGE_TWICE, TEMPLE, OUTAGE, TOMATO, DISTRESS)
    drafts = [classify(payload) for payload in payloads]
    before = [draft.incident.model_copy(deep=True) for draft in drafts]
    result = correlate_incidents(drafts)

    for original, annotated in zip(before, result.drafts):
        assert annotated.incident.evidence == original.evidence
        assert annotated.incident.classification == original.classification
        assert annotated.incident.relevance == original.relevance
        assert annotated.incident.spatial == original.spatial
        assert annotated.incident.confidence == original.confidence
        assert annotated.incident.status is original.status
        assert annotated.incident.review == original.review
        assert original.dedup.decision is DedupDecision.UNRESOLVED
        assert annotated.incident.dedup.decision is not DedupDecision.UNRESOLVED
    assert [d.incident.incident_id for d in result.drafts] == [d.incident.incident_id for d in drafts]


def test_every_supplied_incident_is_fingerprinted_and_carries_a_decision():
    payloads = (SAME_PAGE, SAME_PAGE_TWICE, TEMPLE, OUTAGE, TOMATO, DISTRESS)
    result = correlate(*payloads)

    for draft in result.drafts:
        assert draft.incident.fingerprint
        assert draft.incident.dedup.algorithm_version == correlation.DEDUP_ALGORITHM_VERSION
        assert draft.incident.processing.stage_versions["deduplication"] == "8"
        assert draft.incident.dedup.decision in set(DedupDecision)
    assert set(result.report.decisions) == {d.incident.incident_id for d in result.drafts}


def test_a_cluster_rollup_holds_every_member_record_source_and_evidence_ledger():
    result = correlate(SAME_PAGE, SAME_PAGE_TWICE, TEMPLE, SECOND_PAPER)

    assert len(result.report.clusters) == 2
    for cluster in result.report.clusters:
        assert len(cluster.record_ids) == len(cluster.incident_ids)
        assert len(cluster.source_urls) == len(cluster.incident_ids)
        assert all(cluster.source_urls)
        own = {
            evidence_id
            for member in cluster.incident_ids
            for evidence_id in result.by_incident[member].incident.evidence_by_id()
        }
        assert set(cluster.evidence_ids) == own
        assert {
            result.by_incident[member].incident.dedup.cluster_id for member in cluster.incident_ids
        } == {cluster.cluster_id}


def test_the_report_is_the_same_whenever_the_same_incidents_are_supplied_again():
    payloads = (SAME_PAGE, SAME_PAGE_TWICE, TEMPLE, SECOND_PAPER, OUTAGE, TOMATO, ONION, DISTRESS, RAIN)
    assert correlate(*payloads).report.model_dump_json() == correlate(*payloads).report.model_dump_json()


def test_the_whole_capture_correlates_in_one_pass_without_inventing_a_cluster():
    result = correlate(*F.capture())

    assert [c.record_ids for c in result.report.clusters] == [["NEWS-MDU-0001", "NEWS-MDU-0002"]]
    assert result.report.counts["duplicate"] == 2
    assert "merge_candidate" not in result.report.counts


def test_a_single_incident_is_left_as_unique_rather_than_compared_with_nothing():
    result = correlate(TEMPLE)

    assert result.report.clusters == [] and result.report.relationships == []
    assert result.decision_for("INC-NEWS-MDU-0004") is DedupDecision.UNIQUE
    assert "no pair to compare" in str(result.by_incident["INC-NEWS-MDU-0004"].incident.dedup.notes)


def test_a_url_identifies_a_page_whether_it_was_taken_with_an_anchor_or_a_query():
    assert correlation.page_of("https://A.com/x/#frag") == "https://a.com/x"
    assert correlation.page_of("http://a.com/x/") == "http://a.com/x"
    assert correlation.page_of("https://a.com/x?utm=1") == "https://a.com/x"
    assert correlation.page_of(None) is None
    assert correlation.page_of("/not-a-url") is None
