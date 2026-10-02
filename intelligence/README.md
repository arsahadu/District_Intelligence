# Intelligence Module

AI / NLP intelligence layer for the Collector's District Intelligence Platform.

Owner: Member 1 (AI / Intelligence). This file is the durable technical record of
the module. It documents only what is implemented **and tested** today, plus the
contract guarantees later stages and other modules rely on.

Current state: **Stage 1 complete — contracts.** No pipeline code exists yet.
Nothing here reads a network, a database, or a file.

```
CommonRecord ──> [ Intelligence ] ──> Incident ──> Evidence / Provenance
                     │                    │
                 Stage 1              dedup ──> trends / priority / summaries
                                                   │
                                          Collector Copilot
```

---

## 1. What Stage 1 provides

A versioned, strictly-validated Pydantic v2 data contract for a structured
**Incident** and its **Evidence**, plus the controlled vocabularies that classify
them.

| Package | Purpose |
| --- | --- |
| `intelligence/models/` | Structure: field shapes, types, invariants. |
| `intelligence/config/` | Taxonomy data: event families, department mapping, ordering. |
| `intelligence/tests/` | Executable specification of every rule below. |

The split is deliberate. `models/` must not change when the district adds an
event category; `config/` must not change when a field gains a validator.
`config.vocabularies.vocabulary_integrity_errors()` returns the drift list when
the two disagree, and a test asserts it is empty.

### Files

```
intelligence/
  __init__.py                  module identity; __version__
  requirements.txt             pydantic + pytest, nothing else
  README.md                    this file
  models/
    base.py                    StrictModel, Ratio/Confidence, evidence-reference walker
    enums.py                   28 controlled vocabularies
    evidence.py                Evidence
    language.py                TextRepresentation, LanguageDetection, LanguageInfo,
                               LocalizedText, TitleInfo, SummaryInfo
    temporal.py                TimeValue
    spatial.py                 LocationMention, GisResolution, SpatialHint
    severity.py                SeveritySignal, Severity
    quantities.py              Observation
    actors.py                  Actor
    classification.py          DepartmentHint, RelevanceInfo, ClassificationInfo
    metadata.py                ProcessingMetadata, ReviewInfo, DedupMetadata,
                               ConfidenceSummary
    incident.py                Incident, SCHEMA_VERSION
    __init__.py                public export surface
  config/
    vocabularies.py            TAXONOMY_VERSION, families, department map, helpers
    __init__.py                re-exports
  tests/
    conftest.py                fixtures
    builders.py                real-Tamil-content incident factories
    test_evidence.py                        12 tests
    test_language_and_text.py               16 tests
    test_spatial_severity_time.py           22 tests
    test_classification_and_governance.py   24 tests
    test_incident.py                        15 tests
    test_serialization.py                    8 tests
    test_vocabularies.py                    15 tests
```

---

## 2. Design decisions that shape everything after Stage 1

**Evidence is a record of the source, not a claim by the extractor.**
`Evidence` carries `record_id / source_id / source_type / field / quote /
char_start / char_end / field_text_hash / method / confidence / source_url /
raw_reference / retrieved_at`. A validator enforces that a non-metadata
evidence entry pins a span, that `len(quote) == char_end - char_start`, and that
`span_validation=validated` is impossible without a hash. Offsets are the only
positioning primitive, so the same span arithmetic works for Tamil and English.

**Every derived fact names its method.** `ExtractionMethod` is
`source_metadata | regex | rule | dictionary | statistical | llm | human |
unresolved`. A severity level, an event type, a time value, a language or an
actor without a method and a confidence is a validation error, not a warning.
This is what makes "the LLM said so" structurally unusable as an answer.

**Unresolved is a value, not an absence.** Each vocabulary that can be unknown
has an explicit `UNRESOLVED` / `UNKNOWN` / `NONE` member, and
`Severity.unresolved()` is the shortest legal construction in the module. A
pipeline that cannot decide is never forced to guess in order to validate.

**Hard invariants raise; quality problems are reported.** Reference integrity
(dangling `evidence_ids`, duplicate ids, self-duplication, `status=merged`
without `dedup.duplicate_of`) raises, because those states are incoherent.
Weakness — low confidence, publication-time-only, district from feed config —
surfaces through `inconsistencies()`, `derive_review_reasons()` and
`ReviewInfo`. If both were hard errors, the pipeline would fabricate data to
make them stop; if both were soft, incoherence would persist silently.

**The schema distinguishes four epistemic states.**
*source fact* — `Evidence` with `method=source_metadata`, `TimeValue` with
`semantics=retrieval_time`, `SpatialHint.district_hint_authority=
source_configuration`;
*extracted fact* — a span plus `regex`/`rule`/`dictionary` method;
*inferred/classified* — `ClassificationInfo`, `Severity`, `RelevanceInfo`, each
with `confidence` and, for severity, `is_authoritative` / `confirmed_by`;
*unresolved* — explicit enum members plus `ReviewReason`.

**Original text cannot be discarded.** Once an `Incident` carries any text
representation at all, `LanguageInfo` refuses to build unless at least one has
`TextRole.source`; a `translation` in the same language as the source is
rejected; `derived_from` must point at an existing representation; alignment is
an explicit claim (`offsets_align_with_source`, whose `False` case must carry
confidence), so a translation can never be used to justify a source-anchored
span without saying so. An `Incident` with *no* text yet is legal — that is the
placeholder a pipeline starts from — and it is what
`ReviewReason.NO_SUPPORTING_EVIDENCE` and `inconsistencies()` describe.

**Geography is not ours to invent.** No Intelligence-owned model can write
coordinates: `latitude` / `longitude` / `canonical_place_id` exist only on
`GisResolution`, which requires `resolved_by`, a full coordinate pair, and
confidence with coordinates. `ResolutionState.pending_gis` is the expected
output of the future location stage. `best_event_location_mention_id` must point
at a mention whose `role` is an event-location role, which is how
"Madurai High Court bench" stays out of the event location.

**Severity needs a body of evidence.** A level other than `unresolved` requires
at least one `SeveritySignal`, a rationale, a method and a confidence; an
`unresolved` level may not smuggle in a score. A signal with
`category=unresolved` is rejected, so an empty signal cannot masquerade as
analysis. `Severity` additionally permits human confirmation
(`is_authoritative`, `confirmed_by`) without letting a machine claim it.

**LLM usage is recorded, never hidden.** `ProcessingMetadata` requires
`llm_tasks` and `llm_models` when `llm_used=True` and forbids tasks when it is
`False`. Any LLM-assisted incident carries `ReviewReason.LLM_ASSISTED` through
`derive_review_reasons()`, so a human sees that a model touched it — the
enforcement point for the optional-LLM rule in Stage 10.

**`string_min_length` and `str_strip_whitespace=False` are load-bearing.**
Stripping would break `len(quote) == char_end - char_start`, so whitespace is
preserved verbatim, and Tamil text survives serialisation unescaped
(`test_serialization.py` asserts the raw codepoints appear in the JSON).

---

## 3. Contracts

### Input contract (external, read-only)

The module consumes `CommonRecord` as produced by
`ingestion/app/models/common_record.py`:

```
record_id, source_id, source_type, record_type, title, event_time,
location{raw_text, district, state}, data{content, language}, severity,
status, source_url, retrieved_at, raw_reference
```

Stage 1 does not import it. The mapping is a Stage 8 concern, and the
`Evidence` field names above mirror it one-to-one so the mapping stays mechanical.
Two deliberate non-assumptions:

- `CommonRecord.data["language"]` is copied to
  `LanguageInfo.inherited_language_hint` for audit and *not* trusted as the
  primary language — ingestion hardcodes it. `LanguageInfo.primary_language`
  cannot be resolved without a `LanguageDetection` record that agrees with it,
  so the inherited value has nowhere to leak into.
- `CommonRecord.location.district` maps to
  `SpatialHint.district_hint` with `district_hint_authority=source_configuration`,
  never to an event location. It is recorded as an article-independent hint and
  `inconsistencies()` says so in plain language.

### Output contract

`Incident` — `SCHEMA_VERSION = "1.0"` — 25 fields:

```
schema_version, incident_id, fingerprint, status, origin,
language, title, summary, relevance, classification, actors, observations,
event_time, reported_at, spatial, severity, evidence,
supporting_record_ids, contradicts_record_ids, dedup, confidence, review,
processing, created_at, updated_at
```

Accessors available today: `evidence_by_id()`, `resolve_evidence()`,
`source_quotes()`, `contributing_record_ids()`, `to_storage_document()`,
`derive_review_reasons()`, `apply_review_flags()`, `inconsistencies()`, and the
properties `event_type`, `severity_level`, `primary_language`.

`fingerprint` lives on `Incident`, not on `DedupMetadata` — it identifies the
incident, whereas the dedup decision is metadata *about* it. Both are optional
until Stage 8 computes them.

### Vocabulary contract

`config/vocabularies.py` — `TAXONOMY_VERSION = "2026.10-stage1"`:

- 70 `EventType` codes partitioned into 15 families
  (`EVENT_TYPE_FAMILIES`), each mapped to owning departments
  (`EVENT_TYPE_DEPARTMENTS`) over 33 `Department` codes.
- Event codes are ASCII snake_case domain concepts with no language tokens, so
  a Tamil and an English article about the same incident map to one code.
- `INFORMATIONAL_EVENT_TYPES` isolates ceremony / announcement / transfer /
  sports coverage — newsworthy, not district incidents. This is the
  Stage 6 relevance prior.
- `SEVERITY_LEVEL_ORDER` deliberately excludes `unresolved`, so
  `severity_rank()` cannot silently rank a non-answer; ordering is total over
  the five real levels.
- `vocabulary_integrity_errors()` reports an `EventType` with no family or no
  department mapping, a code in two families, a mapping to a non-member or to an
  empty department tuple, a `Department` with no display label, and a rankable
  `UNRESOLVED` severity. Empty output means the taxonomy and the enums agree.

---

## 4. Verification

```
python -m pytest intelligence/tests -q
```

Result at this commit: **112 passed** in ~0.4s.

The suite is the specification, and it runs against real Dinamalar Madurai
content rather than synthetic English prose. `tests/builders.py` stores
`CONTENT_TA` (a Tamil road-marker protest report containing the
`ADDED :` / `UPDATED :` publish-stamp boilerplate, a transliterated
`லேண்ட்` and a `4800 மனுக்கள்` quantity) and `BENCH_CONTENT_TA` (a Kumbakonam
incident whose text also mentions the Madurai High Court bench). Every
span-backed evidence offset in the fixtures is derived with
`source_text.index(quote)` rather than typed, so the span arithmetic is tested
against real Tamil character counts including combining vowel signs.

Coverage of the twelve required Stage 1 validations:

| Requirement | Evidence in the suite |
| --- | --- |
| Evidence validates | `test_evidence.py` |
| Confidence constrained | `test_confidence_rejects_strings_and_booleans`, `Ratio` guard |
| Incident validates | `test_incident.py` |
| Nested models validate | `test_spatial_severity_time.py`, `test_language_and_text.py` |
| UNRESOLVED / UNKNOWN legal | `test_severity_can_stay_explicitly_unresolved`, `test_time_may_be_entirely_unknown` |
| Tamil stored without corruption | `test_serialization.py` |
| Translation never replaces source | `test_language_and_text.py` |
| Ambiguous location representable | `test_ambiguous_location_is_representable_without_choosing_one` |
| Severity may stay unresolved | `test_spatial_severity_time.py` |
| LLM usage recorded | `test_classification_and_governance.py` |
| Invalid confidence rejected | `test_evidence.py` |
| Serialisation round-trip | `test_serialization.py` |

Three real contract gaps were found and closed while writing these tests:
Pydantic's lax float coercion turned the string `"0.9"` into a valid confidence
(fixed with a `BeforeValidator` numeric-type gate in `models/base.py`);
fingerprint ownership was duplicated between `Incident` and `DedupMetadata`
(resolved in favour of `Incident`); and `LanguageInfo` accepted a resolved
`primary_language` with no `detection` record, which is exactly how ingestion's
hardcoded `data.language` would have been laundered into a detection claim. A
resolved language now requires a detection record that agrees with it.

---

## 5. Known limitations

- **No pipeline.** Nothing converts a `CommonRecord` into an `Incident` yet.
  `tests/builders.py` is hand-written evidence, not extraction output; it exists
  to prove the contract holds against real Tamil text.
- **Optional is still optional.** A field whose value is unknown can be left
  unset where absence and `UNRESOLVED` mean the same thing. Consumers should
  read the enum members, not rely on `None`, for the four epistemic states.
- **`category_scores` is unconstrained beyond key validity.** Keys must be
  `EventType` values; score ranges and normalisation are a Stage 6 concern.
- **No time-zone policy enforcement.** `TimeValue` requires a *declared*
  timezone or an explicit naive-timestamp inconsistency note; it does not decide
  that India is `Asia/Kolkata`. That belongs to Stage 4 configuration.
- **Gazetteer fields are placeholders.** `LocationMention.gazetteer_matched` /
  `gazetteer_source` record a match the module does not perform.
- **Vocabulary is a first draft.** The 70 event types and 33 departments cover
  the categories visible in current samples plus standard district
  administration; they will need review with the collector's office.
  `TAXONOMY_VERSION` exists so a change is visible in stored incidents.
- **Windows console encoding.** Tamil output crashes a cp1252 stdout; run
  diagnostics with `PYTHONIOENCODING=utf-8`. Later stages that print extracted
  text must not assume a UTF-8 console.

## 6. External integration dependencies

Reported, not fixed. The repository outside `intelligence/` was not modified.

1. `ingestion/app/normalizers/news.py` raises on real Madurai data:
   `article_to_common_record()` assigns `SourceArticle.published_at` (an
   `Optional[str]` holding the raw `UPDATED : … ADDED : …` page capture) to
   `CommonRecord.event_time`, which is typed `Optional[datetime]`. Pydantic
   rejects the value, so the connector fails before normalised records are
   written and only raw JSON reaches disk. Reproduced with a two-line article
   fixture; `event_time` is what Intelligence is supposed to *derive*, not
   receive. Until ingestion passes `None` or a parsed value there is no
   `CommonRecord` stream to consume (owner: ingestion).
2. `record_id` is `f"NEWS-MDU-{index:04d}"`, derived from the scrape's
   enumeration index, so it changes whenever feed order or page size changes.
   `Evidence.record_id` and `Incident.supporting_record_ids` assume stable ids,
   so dedup and cross-source linking are not trustworthy until the id is
   content-derived (owner: ingestion).
3. `Location(raw_text="Madurai", district="Madurai", state="Tamil Nadu")` is
   hardcoded for every article. Contract-wise this is handled
   (`district_hint_authority=source_configuration`), but district-level analytics
   will be wrong until ingestion stops asserting it (owner: ingestion).
4. `CommonRecord.raw_reference` has two meanings in the repo: `news.py` sets it
   to `article.url`, while `backend/tests/test_records_api.py:30` posts
   `"data/raw/dinamalar/example.json"`. `Evidence.raw_reference` is a free-form
   string and tolerates both, but provenance tooling needs one definition
   (owner: ingestion + backend).
5. `backend/alembic/env.py:7,18` binds `target_metadata` to
   `app.db.base.Base`, but `backend/app/models/common_record.py:5` declares a
   *second* `declarative_base()` and `CommonRecord` inherits from that one. The
   `common_records` table is therefore invisible to Alembic autogenerate. Not an
   Intelligence defect, but it constrains the Stage 9 persistence choice
   (owner: backend).
6. No LLM API key or OCR toolchain is available in this environment, which the
   design assumes and does not depend on.

## 7. Next: Stage 2 — evidence span primitive

Stage 2 turns the `Evidence` *record* into an evidence *producer*: a
`TextSpan` / `EvidenceBuilder` layer that takes a field's original text, a
matched quote and an extraction method, and returns a hash-pinned `Evidence`
with offsets computed — never hand-typed — plus a verifier that re-hashes the
source field and transitions `span_validation` between `validated` and
`mismatch`. Expected files: `intelligence/extraction/spans.py` (offset
arithmetic over combining characters, field hashing, Windows-safe UTF-8 IO) and
its tests. Stage 2 adds no extraction rules; those are Stage 3 onward.
