# District Intelligence — Intelligence Module

## 1. Purpose

This module is the AI/NLP intelligence layer of the Collector's District Intelligence Platform. It takes
source-independent `CommonRecord` objects from the data integration layer and transforms them into structured,
evidence-grounded `Incident`s: what happened, when, where the text says it happened, who the text names, and the exact
source span behind each claim.

`Incident` is the primary intelligence output: the object the platform persists, GIS writes coordinates into, and
later stages reason over. Classification and relevance, severity, dedup and clustering, trends, LLM reasoning and the
chatbot build on that contract and are planned, not implemented. Extraction is deterministic — rules and controlled
vocabularies, every important result citing a range in the untouched source text, nothing unsupported invented.

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
  ├── Place Mentions                  — raw places the text names, with roles
  ├── Actor Mentions                  — parties the text ties to the event
  ├── Event Classification            — planned (Stage 7)
  ├── Relevance / Severity            — planned (Stage 7)
  ├── Deduplication / Clustering      — planned (Stage 8)
  ├── Trends / Briefing               — planned (Stage 11)
  └── LLM / Chatbot                   — planned (Stages 10, 12)
  ↓
Incident Intelligence                 — incident + evidence ledger + confidence + review flags
  ↓
Platform / GIS / Collector Workspace
```

Two seams are implemented: `mapping.map_record(record)` builds a candidate incident from a record, and
`mapping.enrich_incident(draft)` adds its places and parties, each returning an `IncidentDraft`.

## 3. Source Contract

`CommonRecord` is an **external, source-independent input contract** owned by Data Integration; any feed that can be
normalised into one enters the same intelligence pipeline.

| Source | What it contributes |
| --- | --- |
| Dinamalar (Tamil news) | article text, headline, publish stamps |
| IMD weather | warnings and measurements |
| Tamil Nadu agriculture / market (AgMarkNet) | price and distress records |
| Future departmental feeds (PWD, power, revenue, police) | complaints, tickets, notices |
| Future disaster feeds (flood control, nowcasts) | event and warning records |

Intelligence never imports ingestion code or depends on a connector's internals. `mapping/record_input.py` reads
records by declared field path (`title`, `data.content`, `data.language`, `location.district`, `event_time`,
`retrieved_at`, `source_url`, `record_id`) against a structural protocol, so a renamed or missing field fails loudly
instead of surfacing as a silent `None`.

## 4. Intelligence Output

An `Incident` is one candidate account of one event, derived from one or more source records.

| Section | Holds |
| --- | --- |
| `incident_id`, `fingerprint`, `status`, `origin` | stable readable identity, dedup fingerprint, lifecycle state, pipeline/manual/imported origin |
| `title`, `language` | headline as published plus optional display translation; primary language, script, detection reasoning, text representations |
| `event_time`, `reported_at` | partially-known instants with semantics, precision and their own evidence |
| `spatial` | raw place mentions, district hint and its authority, the GIS write-back slot |
| `actors` | named parties with the role the text gave them |
| `relevance`, `classification` | whether this is an incident, event type, scores, department hints — contract only today |
| `severity`, `observations` | evidence-weighted level or an explicit unknown; counts and amounts the source stated |
| `evidence`, `supporting_record_ids` | every quote pinned to a source field with offsets and validation state; the records it came from |
| `confidence`, `review`, `processing` | component scores and unresolved field count, whether a human should look, stage/config/input hashes |

Unsupported information stays unresolved — `severity.level = "unresolved"`, empty `classification`, an `event_time`
with an explicit absence reason, `resolution_state = "pending_gis"`. A gap is stated, never papered over.

## 5. Stage Progress

| Stage | Purpose | Status |
| --- | --- | --- |
| 1 | Intelligence contracts: `Incident`, `Evidence`, enums, event taxonomy | Complete |
| 2 | Evidence and span extraction: derived offsets, verification, revalidation | Complete |
| 3 | Language, normalisation and Tamil text handling | Complete |
| 4 | Temporal expression extraction and time roles | Complete |
| 5 | `CommonRecord` → candidate `Incident` mapping | Complete |
| 6 | Place and actor mentions from the record's own text | Complete |
| 7 | Relevance and event classification | Planned |
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
- **Evidence and metadata** — one ledger per record, offsets derived by quoting and never typed, every value citing it.
- **Source provenance** — record id, source url and raw reference carried through to point back at the story.
- **Candidate assembly** — title, language, spatial stub, severity, confidence, processing and review; `MappingPolicy`
  supplies timezone, body-text fields and the incident id prefix.
- **Validation and review** — failed checks, ambiguities and unresolved sections surface as `review.reasons` and warnings.

**Source `event_time` is not trusted for news.** Current Tamil news ingestion puts an article's `ADDED` timestamp into
`CommonRecord.event_time` — a publication moment, not an event one. Stage 5 preserves it, warns about it, and never
lets it become an article's event time; that has to come from the body text.

## 7. Stage 6 — Place and Actor Mentions

`enrich_incident(draft, policy)` adds what the record's own text mentions to a Stage 5 draft, changing nothing Stage 5
settled. Both readers work over one versioned place, entity and cue vocabulary (`config/mention_words.py`).

### Place extraction

- Extracts **raw place mentions** from title and body: the dateline, a case-marked type word (`மதுரை மாவட்டத்தில்`),
  a known name, a name in front of a type word, and Latin spellings for English and Tanglish surfaces.
- Identifies the roles the text supports: `reporting_origin`, `event_location`, `event_container`, `institution_name`,
  `actor_affiliation`, `mentioned_only`, `unresolved`.
- Records the granularity the text claims, keeps `unknown` when the text does not say what kind of place it is, and
  preserves the surface exactly as printed beside any display form.
- Attaches `Evidence` to every mention, and refuses a bare generic type word with its reason kept (`generic_unlocated`,
  `plural_generic`, `blocked_oblique`).
- **Does not perform GIS resolution** — no coordinates, canonical ids, boundaries or administrative parents (§9).

### Actor extraction

- Extracts people, officials, government bodies, courts, police stations, hospitals, local bodies, companies, NGOs,
  community groups, parties and media outlets where the text supports them (`ActorType`), each with the role the text
  gave it (`ActorRole`: `reported_by`, `decision_maker`, `responding_authority`, `affected_party`, `beneficiary`, …).
- Uses entity heads plus context rules — a title licenses the bare name behind it, a collective head names a group, a
  cue verb ties a party to the event, precedence decides whose word survives when one surface is cued twice.
- Does not invent actors on thin evidence: an unattached party stays a visible refusal (`uncued`), an affiliation is
  credited only when the modifier can mean exactly one place, a response cue only goes to a body that can respond.
- Keeps the phrase span that produced the actor and the cue span that justified its role — two citations per actor.

### Evidence

```
mention / actor  →  exact source span  →  Evidence  →  source record
```

`enrich_incident` merges new evidence into the incident's ledger and **re-verifies the whole ledger before returning**
the enriched draft, then recomputes the unresolved field count and re-applies review flags. A mention whose span does
not reproduce its quote is not an output.

## 8. Language and Tamil NLP

The foundation these readers sit on is deterministic Tamil NLP, not a model:

- **Language and script detection** from letter shapes; `und` for undetermined, `mul` for genuinely multilingual text.
- **Normalisation with source-position preservation** — derived text maps back to the original, so matches cite real offsets.
- **Tamil morphology** — case and postposition suffix candidates, stem variants, clitic and plural tails.
- **Transliteration** — script table plus curated vocabulary giving Latin candidates for a Tamil word.
- **Tamil temporal expressions** — month and day-part names, Tamil numerals, elapsed and counted surfaces, day-first dates.
- **Tamil place and entity surfaces** — locative marks, postpositions, qualifiers, noun and verb blocklists.

Original Tamil text is always kept; normalisation and transliteration only widen what extraction can reach, never
replacing the source in evidence or in output.

## 9. GIS Boundary

Stage 6 identifies **`திருமங்கலம்`** as a raw place mention: the printed surface, its span, the role the text gives it,
the granularity the text claims. Canonical geography is the GIS module's job.

```
Intelligence                            GIS
"திருமங்கலம்" (mention + span)  →  canonical geographic entity
                                    → Taluk / Block / Village
                                    → coordinates and boundaries
```

Intelligence writes `resolution_state = "pending_gis"` and leaves the `gis` write-back slot on `SpatialHint` unset for
GIS. It invents no coordinates, GIS ids, administrative parents or geometry, and never calls a name a town when the
text does not say.

## 10. Evidence and Traceability

Evidence is why the output can be acted on: a Collector's dashboard drives administrative action, so an incident that
cannot be traced to its source is worse than no incident. Every extracted fact that matters carries:

- **source record id** — which `CommonRecord` it came from (`Incident.supporting_record_ids`);
- **source url / raw reference** — where that record was fetched from;
- **source field** — the field path and a hash of the whole field text;
- **exact text and span** — the verbatim quote, character offsets, validation state (`unvalidated`, `validated`, `mismatch`);
- **provenance** — extraction method, modality, confidence, provider, stage versions;
- **verification** — spans replayed against the field's current text, so a source edit surfaces as a failed check.

`extraction/spans.py` is the only sanctioned producer of `Evidence`; hand-typed offsets fail the same check.

## 11. AI / NLP / LLM Roadmap

The implemented stages are rule-based NLP over controlled vocabularies — predictable, verifiable, debuggable. No LLM is
in use today; later stages may introduce one where it earns its place:

- ambiguous event classification and relevance when cues conflict (Stage 7);
- cross-source reasoning when several records describe one event (Stage 8);
- complex summaries and Collector briefings (Stages 10-11);
- the Collector chatbot answering over stored incidents (Stage 12).

LLM output is validated the same way: it must reproduce a source span or be rejected, and is marked probabilistic in provenance.

## 12. Testing

```bash
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

642 tests pass (verified 2026-10-03) and `compileall` is clean. They cover the Stage 1 contracts, span arithmetic and
verification, the language / normalisation / morphology / transliteration layer, temporal reading and record mapping,
and an end-to-end replay of the real October 2026 Madurai news capture. Stage 6 alone is 184 tests: word reading,
place mentions and refusals, actors and cues, and enrichment with re-verification and the GIS boundary.

## 13. Current Limitations

- Lexical extraction bounds recall: an unseen place or entity form never becomes a candidate at all.
- Negation and context understanding are limited — "denied the permission" still reads as an official response.
- No coreference: a pronoun or a bare re-mentioned name adds nothing to a party that already has a span.
- No canonical GIS resolution — raw mentions and `pending_gis` only (§9).
- No event classification, relevance or severity derivation; the Stage 1 taxonomy is a contract only.
- No deduplication or clustering: one record yields one incident even when two describe the same event.
- No LLM reasoning, summaries or chatbot.
- The prose gate keeps only text after the last dropped stamp run, so a pre-stamp lede is lost.

## 14. Integration Boundaries

| Owner | Responsibility |
| --- | --- |
| Ahad — Data Integration | Source → `CommonRecord` (connectors, normalisation, fetch scheduling) |
| Prem — Intelligence | `CommonRecord` → Incident intelligence (this module) |
| Alagan — GIS | Place mentions → canonical geography, coordinates, visualisation |
| Alex — Platform | Incident intelligence → PostgreSQL, FastAPI, Collector workspace |

Intelligence owns no connector, table, map layer or screen; teammate-module defects are reported here with file:line
evidence rather than fixed here.

## 15. Next Stage

Next: **Stage 7 — Relevance and Event Classification.** Use the evidence Stages 4-6 already collect to decide, with
citations, whether a record is an incident, which event type it is, and which departments are plausible owners —
severity following in the same stage.
