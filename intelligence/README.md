# District Intelligence — Intelligence Module

## 1. Purpose

This module is the AI/NLP intelligence layer of the Collector's District Intelligence Platform. It takes
source-independent `CommonRecord` objects from the data integration layer and transforms them into structured,
evidence-grounded `Incident`s: whether the text reports an event at all, what happened, when and where the text says it
did, who the text names, and the exact source span behind each claim.

`Incident` is the primary output: what the platform persists, what GIS writes coordinates into, what later stages reason
over. Severity, dedup and clustering, trends, LLM reasoning and the chatbot build on that contract and are still
planned. Extraction is deterministic — rules and controlled vocabularies, every important result citing a range in the
untouched source text, nothing unsupported invented.

## 2. Architecture

```
Source (Dinamalar, IMD weather, AgMarkNet markets, departmental feeds)
  ↓
Connector + normaliser                — Data Integration
  ↓
CommonRecord                          — external input contract
  ↓
Intelligence                          — this module
  ├── Language / Text                 — script detection, normalisation, morphology, stamps
  ├── Temporal Extraction             — surfaces, then event / publication / reporting roles
  ├── Incident Assembly               — one record in, one candidate incident out
  ├── Place / Actor Mentions          — raw places and named parties, with roles
  ├── Relevance / Classification      — is this an incident, and which event type
  ├── Severity                        — planned
  ├── Deduplication / Clustering      — planned (Stage 8)
  ├── Trends / Briefing               — planned (Stage 11)
  └── LLM / Chatbot                   — planned (Stages 10, 12)
  ↓
Incident Intelligence                 — incident + evidence ledger + confidence + review flags
  ↓
Platform / GIS / Collector Workspace
```

Three seams are implemented: `mapping.map_record(record)` builds a candidate incident from a record,
`mapping.enrich_incident(draft)` adds its places and parties, and `mapping.classify_incident(draft)` decides whether it
is an incident and which event type the text supports — each returning an `IncidentDraft`.

## 3. Source Contract

`CommonRecord` is an **external, source-independent input contract** owned by Data Integration; any feed that can be
normalised into one enters the same intelligence pipeline.

| Source | What it contributes |
| --- | --- |
| Dinamalar (Tamil news) | article text, headline, publish stamps |
| IMD weather | warnings and measurements |
| Tamil Nadu agriculture / market (AgMarkNet) | price and distress records |
| Future departmental and disaster feeds (PWD, power, revenue, police, flood control) | complaints, tickets, notices, warnings |

Intelligence never imports ingestion code or depends on a connector's internals. `mapping/record_input.py` reads
records by declared field path (`title`, `data.content`, `data.language`, `location.district`, `event_time`,
`retrieved_at`, `source_url`, `record_id`, and the feed's own `source_type` and `record_type`) against a structural
protocol, so a renamed or missing field fails loudly instead of surfacing as a silent `None`.

## 4. Intelligence Output

An `Incident` is one candidate account of one event, derived from one or more source records.

| Section | Holds |
| --- | --- |
| `incident_id`, `fingerprint`, `status`, `origin` | stable readable identity, dedup fingerprint, lifecycle state, pipeline/manual/imported origin |
| `title`, `language` | headline as published plus optional display translation; primary language, script, detection reasoning, text representations |
| `event_time`, `reported_at` | partially-known instants with semantics, precision and their own evidence |
| `spatial` | raw place mentions, district hint and its authority, the GIS write-back slot |
| `actors` | named parties with the role the text gave them |
| `relevance`, `classification` | whether this is an incident, the event type the text supports, ranked candidates, department hints |
| `severity`, `observations` | evidence-weighted level or an explicit unknown; counts and amounts the source stated |
| `evidence`, `supporting_record_ids` | every quote pinned to a source field with offsets and validation state; the records it came from |
| `confidence`, `review`, `processing` | component scores and unresolved field count, whether a human should look, stage/config/input hashes |

Unsupported information stays unresolved — `severity.level = "unresolved"`, `event_type = "unresolved"` when no cue
carries the type, an `event_time` with an explicit absence reason, `resolution_state = "pending_gis"`. A gap is stated,
never papered over.

## 5. Stage Progress

| Stage | Purpose | Status |
| --- | --- | --- |
| 1 | Intelligence contracts: `Incident`, `Evidence`, enums, event taxonomy | Complete |
| 2 | Evidence and span extraction: derived offsets, verification, revalidation | Complete |
| 3 | Language, normalisation and Tamil text handling | Complete |
| 4 | Temporal expression extraction and time roles | Complete |
| 5 | `CommonRecord` → candidate `Incident` mapping | Complete |
| 6 | Place and actor mentions from the record's own text | Complete |
| 7 | Relevance and event classification | Complete |
| 8 | Deduplication and incident clustering | Planned |
| 9 | Persistence and platform integration | Planned |
| 10 | Optional LLM intelligence | Planned |
| 11 | Trends, alerts and briefing | Planned |
| 12 | Collector chatbot | Planned |

## 6. Stage 5 — CommonRecord → Incident

`map_record(record, policy)` assembles the candidate incident:

- **Structural adapter** — reads the record by field path, keeps text verbatim, hashes what it read for re-runs.
- **Language detection** — script evidence over code points; the feed's own label is kept, disagreement recorded.
- **Text and boilerplate handling** — publish stamps (`ADDED :`, `UPDATED :`) split off the body as publication metadata.
- **Temporal extraction** — event time from the body when the text supports it, else an explicit absence; retrieval
  time is metadata, never event time.
- **Evidence and provenance** — one ledger per record, offsets derived by quoting and never typed, every value citing
  it, and the record id, url and raw reference carried through to point back at the story.
- **Candidate assembly** — title, language, spatial stub, severity, confidence, processing and review; `MappingPolicy`
  supplies timezone, body-text fields and the incident id prefix.
- **Validation and review** — failed checks, ambiguities and unresolved sections surface as `review.reasons` and warnings.

**Source `event_time` is not trusted for news.** Current Tamil news ingestion puts an article's `ADDED` timestamp into
`CommonRecord.event_time` — a publication moment, not an event one. Stage 5 preserves it, warns about it, and never
lets it become an article's event time; that has to come from the body text.

## 7. Stage 6 — Place and Actor Mentions

`enrich_incident(draft, policy)` adds what the record's own text mentions to a Stage 5 draft, changing nothing Stage 5
settled. Both readers work over one versioned place, entity and cue vocabulary (`config/mention_words.py`), and every
mention and party cites the span that produced it: a mention whose span will not reproduce its quote is not an output.
The draft's whole ledger is re-verified before it returns, then the unresolved count and review flags are recomputed.

### Place extraction

- Extracts **raw place mentions** from title and body: the dateline, a case-marked type word (`மதுரை மாவட்டத்தில்`),
  a known name, a name in front of a type word, and Latin spellings for English and Tanglish surfaces.
- Records the role the text supports (`reporting_origin`, `event_location`, `event_container`, `institution_name`,
  `actor_affiliation`, `mentioned_only`, `unresolved`) and the granularity it claims, keeping `unknown` when the text
  does not say what kind of place it is and the surface exactly as printed beside any display form.
- Refuses a bare generic type word with its reason kept (`generic_unlocated`, `plural_generic`, `blocked_oblique`), and
  **performs no GIS resolution** — no coordinates, canonical ids, boundaries or administrative parents (§10).

### Actor extraction

- Extracts people, officials, government bodies, courts, police stations, hospitals, local bodies, companies, NGOs,
  community groups, parties and media outlets where the text supports them (`ActorType`), each with the role the text
  gave it (`ActorRole`: `reported_by`, `decision_maker`, `responding_authority`, `affected_party`, `beneficiary`, …).
- Uses entity heads plus context rules — a title licenses the bare name behind it, a collective head names a group, a
  cue verb ties a party to the event, precedence decides whose word survives when one surface is cued twice.
- Does not invent actors on thin evidence: an unattached party stays a visible refusal (`uncued`), an affiliation is
  credited only when the modifier can mean exactly one place, a response cue only goes to a body that can respond.
- Cites twice — the phrase span that produced the actor and the cue span that justified its role.

## 8. Stage 7 — Relevance and Event Classification

`classify_incident(draft, policy)` reads what Stages 4-6 settled — the feed's own kind, the district the record states,
its place and party mentions — and answers the two questions a Collector acts on: does this record carry an incident,
and which event type does the text support. Still no model: one versioned cue vocabulary (`config/event_type_cues.py`)
and one scoring rule, over the Stage 1 taxonomy.

### Relevance

A ladder; the first rung that matches wins, and each rung cites the span that put the record there.

- **Paid space and boilerplate** (`விளம்பரம்`, `advertisement`, `subscribe`) with no event word → `not_incident`.
- **Observation feeds** — IMD forecasts, warnings and readings, market price rows, dashboard rows — state the
  district's own condition, so the feed's declared kind settles relevance without an event word to find.
- **A PRIMARY event cue** anchored to the district the record claims → `incident`.
- **Contested districts, markers beside cues, SUPPORTING-only evidence, no district claimed** → `unsure`, which
  asserts nothing and carries no confidence.

### Event classification

- 246 cue surfaces over the taxonomy, each PRIMARY (weight 0.80) or SUPPORTING (0.30). A word that only names an
  institution or a topic (`சம்பவம்`, `நிகழ்வு`, `பிரச்னை`, `case`) decides nothing: a type has to be said, not hinted.
- A type scores its best match plus 0.05 per distinct extra entry (up to 0.15), capped at 0.95. It resolves at 0.55
  and only while the runner-up sits 0.10 behind; a tie, a contested key or a low top score leaves
  `event_type = "unresolved"` and reports the ranked `category_scores` and `secondary_event_types` instead.
- Department hints come from the type's family and from bodies whose name states its office (police station,
  hospital, school). A local body or government body could answer to two offices, so it hints nothing.
- Record kind is a candidate, never a verdict: `market_price` names `market_price_distress` at 0.35, deliberately
  under the floor, so a price row is tracked without being invented into an event it does not describe.

Every decision cites a span: `record_type` and `source_type` are registered as citable fields, the district citation
reuses Stage 5's own evidence id instead of minting a second one, and the ledger re-verifies before the draft returns.
Over the October 2026 capture that lands as 354 relevant, 4 unsure and 1 unresolved of 359 records, with 5 event types
resolved on evidence (a protest, an accident, a power outage, a ceremony, a court order). The other 354 stay
unresolved on purpose: 349 market and weather observations whose ranked candidate and score are recorded instead of an
asserted type, and 5 news records that named no event type or only a weak candidate. Severity, GIS and dedup stay
unresolved.

## 9. Language and Tamil NLP

The foundation these readers sit on is deterministic Tamil NLP, not a model: **script and language detection** from
letter shapes (`und` undetermined, `mul` genuinely multilingual); **normalisation that preserves source positions**, so
derived text still maps back to the offsets it came from; **morphology** — case and postposition suffix candidates,
stem variants, clitic and plural tails; **transliteration** giving Latin candidates for a Tamil word; **temporal
surfaces** — Tamil month and day-part names, numerals, elapsed and counted forms, day-first dates; and **place and
entity surfaces** — locative marks, postpositions, qualifiers, noun and verb blocklists. Original Tamil text is always
kept: normalisation and transliteration only widen what extraction can reach, never replacing the source in output.

## 10. GIS Boundary

Stage 6 identifies **`திருமங்கலம்`** as a raw place mention — the printed surface, its span, the role and granularity the
text claims — and Stage 7 only ever asks whether the record states one district without contradiction. Canonical
geography belongs to the GIS module: the entity, its Taluk / Block / Village parents, coordinates and boundaries.

Intelligence writes `resolution_state = "pending_gis"` and leaves the `gis` write-back slot on `SpatialHint` unset for
GIS. It invents no coordinates, GIS ids, administrative parents or geometry, and never calls a name a town when the
text does not say.

## 11. Evidence and Traceability

Evidence is why the output can be acted on: a Collector's dashboard drives administrative action, so an incident that
cannot be traced to its source is worse than no incident. Every extracted fact that matters carries the source record
id (`Incident.supporting_record_ids`), the url and raw reference it was fetched from, the field path plus a hash of the
whole field text, the verbatim quote with character offsets and a validation state (`unvalidated`, `validated`,
`mismatch`), the provenance that produced it (method, modality, confidence, provider, stage versions), and re-checks:
spans replay against the field's current text, so a source edit surfaces as a failed check.

`extraction/spans.py` is the only sanctioned producer of `Evidence`; hand-typed offsets fail the same check.

## 12. AI / NLP / LLM Roadmap

The implemented stages are rule-based NLP over controlled vocabularies — predictable, verifiable, debuggable, with no
model in the loop. Stage 7 leaves a conflict unresolved rather than guessing at it, and later stages may introduce a
model for exactly those cases: cues that tie or an unseen phrasing (Stage 10), several records describing one event
(Stage 8), briefings worth summarising (Stages 10-11), the Collector chatbot (Stage 12). LLM output would be validated
the same way: it must reproduce a source span or be rejected, and is marked probabilistic in provenance.

## 13. Testing

```bash
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

736 tests pass (verified 2026-10-03) and `compileall` is clean. They cover the Stage 1 contracts, span arithmetic and
verification, the language / normalisation / morphology / transliteration layer, temporal reading, record mapping, place
and actor reading, and an end-to-end replay of the real October 2026 Madurai capture. Stage 6 is 184 tests; Stage 7 is
94 — vocabulary integrity, span-anchored matching and refusals, scoring floors and tie margins, the relevance ladder
over news, weather and market records in three scripts, and the chain replayed on the real fixtures with Stage 5 and 6
output unchanged field for field. Run against every normalised record on disk (359 distinct October 2026 rows: 342
market, 10 news, 7 weather) the chain classifies all of them with no failure, no span that will not replay and no drift
on a second pass, in about 3 s.

## 14. Current Limitations

- Lexical extraction bounds recall: an unseen place, entity or event form never becomes a candidate at all.
- Negation and context understanding are limited — "denied the permission" still reads as an official response.
- No coreference: a pronoun or a bare re-mentioned name adds nothing to a party that already has a span.
- No canonical GIS resolution — raw mentions and `pending_gis` only (§10).
- The taxonomy has no type for "rainfall warning" or "today's price", so those records stay relevant but unresolved,
  their candidate recorded in `category_scores`.
- Relevance trusts the district the feed claims, so a mislabelled feed looks relevant.
- A habitual statement can score as an event: a report on the accidents a junction *causes* reads as `vehicle_accident`
  with a real span behind it — vocabulary calibration, not a phantom quote.
- Generic-word refusals are surface-exact, so a sandhi form (`இச்சம்பவம்`) escapes them; an audit line, not a decision.
- No severity derivation, no deduplication or clustering, no LLM reasoning, summaries or chatbot — Stages 8 onward.
- The prose gate keeps only text after the last dropped stamp run, so a pre-stamp lede is lost.

## 15. Integration Boundaries

| Owner | Responsibility |
| --- | --- |
| Ahad — Data Integration | Source → `CommonRecord` (connectors, normalisation, fetch scheduling) |
| Prem — Intelligence | `CommonRecord` → Incident intelligence (this module) |
| Alagan — GIS | Place mentions → canonical geography, coordinates, visualisation |
| Alex — Platform | Incident intelligence → PostgreSQL, FastAPI, Collector workspace |

Intelligence owns no connector, table, map layer or screen; teammate-module defects are reported here with file:line
evidence rather than fixed here.

## 16. Next Stage

Next: **Stage 8 — Deduplication and Incident Clustering.** The capture already carries the same story twice, once per
comment anchor: decide, from fingerprints, spans and the relevance and type Stage 7 settled, when records describe one
event, and carry `supporting_record_ids` and `status` forward without losing which source said what.
