# Intelligence Module

AI / NLP intelligence layer for the Collector's District Intelligence Platform.

Owner: Member 1 (AI / Intelligence). This file is the durable technical record of
the module. It documents only what is implemented **and tested** today, plus the
contract guarantees later stages and other modules rely on.

Current state: **Stages 1-2 complete — contracts plus the evidence span
primitive.** There is still no CommonRecord → Incident pipeline: nothing here
reads a network, a database, or a file, and no text is classified, normalised or
extracted yet.

```
CommonRecord ──> [ Intelligence ] ──> Incident ──> Evidence / Provenance
                     │                    │
                Stage 1 + 2            dedup ──> trends / priority / summaries
                                                   │
                                          Collector Copilot
```

---

## 1. What is implemented

**Stage 1** — a versioned, strictly-validated Pydantic v2 data contract for a
structured **Incident** and its **Evidence**, plus the controlled vocabularies
that classify them.

**Stage 2** — `intelligence/extraction/spans.py`: the evidence span primitive
that *produces* that `Evidence`, deriving offsets and hashes from the source
text and verifying them against it later.

| Package | Purpose |
| --- | --- |
| `intelligence/models/` | Structure: field shapes, types, invariants. |
| `intelligence/config/` | Taxonomy data: event families, department mapping, ordering. |
| `intelligence/extraction/` | Evidence production: spans, hashes, verification. |
| `intelligence/tests/` | Executable specification of every rule below. |

The split is deliberate. `models/` must not change when the district adds an
event category; `config/` must not change when a field gains a validator;
`extraction/` holds the only code allowed to turn "the source says X" into an
`Evidence` object. `config.vocabularies.vocabulary_integrity_errors()` returns
the drift list when models and taxonomy disagree, and a test asserts it is empty.

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
  extraction/
    spans.py                   SourceField, Span, locate, build_evidence,
                               compute_field_hash, verify_evidence, revalidate
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
    test_spans.py                           55 tests  (Stage 2)
```

---

## 2. Design decisions that shape every later stage

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

## 4. Stage 2 — the evidence span primitive

`intelligence/extraction/spans.py` is the only code allowed to produce an
`Evidence` that quotes a source. An extractor says *what the source says*; this
layer decides *where it says it*.

```
SourceField (record + field + untouched text)
        │
        ├── locate(text, quote, occurrence=None) ──> Span(char_start, char_end, quote)
        │
        └── build_evidence(source, quote, method=…, confidence=…)
                        │  offsets + field hash derived here, never typed
                        ▼
                   Evidence(span_validation=validated)
                        │
                        ├── verify_evidence(evidence, current_field_text) ──> SpanCheck
                        └── revalidate(evidence, current_field_text)      ──> (Evidence, SpanCheck)
```

### Inputs the producer accepts

`SourceField` carries `record_id`, `source_id`, `source_type`, `field` (dotted
path, same convention as `Evidence.field`), `text` (the untouched original), and
optional `source_url`, `raw_reference`, `retrieved_at`. `build_evidence` adds the
`quote`, the `method`, an optional `confidence`, an optional `occurrence`, and
an optional `evidence_id` / `modality` / `notes`. That is the full list the
future pipeline needs; nothing else is inferred.

`SourceField` is frozen on purpose. A span is only meaningful against the text it
was cut from, so mutating `text` after building Evidence must be impossible
rather than merely inadvisable.

### How offsets are calculated

Python character indices into the text exactly as stored. Not byte offsets, not
code-unit pairs, not indices into a normalised copy. A Tamil grapheme is often
several code points (`நீ` is `ந` + U+0BC0, `ம்` is `ம` + U+0BCD), so byte
arithmetic would land the end offset mid-syllable; `test_offsets_are_code_points_not_bytes`
pins that distinction against real fixture text.

Two entry points, one rule:

- `build_evidence(source, quote, …)` searches for the quote and derives the
  range. The caller supplies no numbers.
- `build_evidence_at(source, span, …)` is for stages that already know a range
  (a regex match object, an OCR word box mapped to a transcript). It trusts the
  span for *where*, never for *whether*: the range must reproduce
  `span.quote` in the source text or the call raises. A lying range cannot
  become Evidence.

The Stage 1 invariant `len(quote) == char_end - char_start` is what makes a
hand-written span detectable, and this layer is what makes it unnecessary.

### How the hash is used

`field_text_hash` is the sha256 of the UTF-8 encoding of the **whole field**,
not of the quote. Two quotes from one article share it, so one recomputation
verifies every Evidence derived from that field, and a changed headline or an
edited paragraph is detectable even when the recorded offsets still slice to
something plausible. The definition is identical to the one Stage 1's fixtures
used (`tests/builders.field_hash`), verified by
`test_stage_two_output_matches_the_handbuilt_stage_one_fixture`.

### Verification, and why a mismatch is never repaired

`verify_evidence` replays one Evidence against the text the field holds now and
returns a `SpanCheck(validation, reason, expected_hash, actual_hash)`:

| Condition | `span_validation` |
| --- | --- |
| hash matches and `text[start:end] == quote` | `validated` |
| hash differs (field edited after extraction) | `mismatch` |
| hash matches but offsets hold other text | `mismatch` |
| no hash recorded, so field identity is unproven | `unvalidated` |
| no span at all (field-level metadata provenance) | `not_applicable` |

`revalidate` returns the Evidence rebuilt with that verdict plus the reason in
`notes`, and returns the *same object* when the verdict is unchanged so repeated
replays cannot append the same note forever. It never re-locates the quote:
silently finding "மதுரையில்" somewhere else in an edited article would make an
altered source look like it always said what we recorded. Re-anchoring is an
explicit re-extraction through `build_evidence`, which mints a new id.

### Ambiguity

`find_spans` reports every occurrence, overlapping included (`"aa"` in `"aaa"`
is two). `locate` with more than one match and no `occurrence` index raises
`AmbiguousQuoteError`, which carries the offsets in the message and on
`.occurrences`, so the caller can pick deliberately (`occurrence=1`, negative
indices allowed) instead of inheriting whichever match Python found first. An
out-of-range index raises `OccurrenceOutOfBoundsError`.

Rejections: `EmptyQuoteError` for `""` and for whitespace-only quotes;
`QuoteNotFoundError` for a quote that is absent, matched case-sensitively and
without Unicode normalisation. When a quote *would* match after normalisation,
the error says which form (NFC/NFD/NFKC/NFKD) would have made it work — a
diagnosis, not a repair. Stage 3 gets its own normalised representations; spans
stay anchored to the untouched original.

### Producer discipline

- `method=unresolved` cannot produce Evidence: "unresolved" is a state on a fact,
  not a way of quoting a source.
- `llm` and `statistical` output must carry a confidence; exact-match methods
  (`regex`, `rule`, `dictionary`, `source_metadata`, `human`) may omit it rather
  than imply a false precision of 1.0.
- `evidence_id` defaults to a deterministic digest of
  record + field + offsets + method + quote, so re-running a stage over unchanged
  text reuses the id instead of creating a second identity for one quote — which
  is what lets Stage 8 merge incidents without another reconciliation step.
- `modality` is declarable (`audio_transcript`, `image`, …) so an OCR or audio
  stage feeds the same span machinery without changing it.

CommonRecord → Intelligence integration is still a later stage: this layer takes
field text from its caller and does not read, resolve or import a `CommonRecord`.

---

## 5. Verification

```
python -m pytest intelligence/tests -q
```

Result at this commit: **167 passed** in ~0.4s (112 Stage 1 contracts, 55 Stage 2
spans).

The suite is the specification, and it runs against real Dinamalar Madurai
content rather than synthetic English prose. `tests/builders.py` stores
`CONTENT_TA` (a Tamil road-marker protest report containing the
`ADDED :` / `UPDATED :` publish-stamp boilerplate, a transliterated
`லேண்ட்` and a `4800 மனுக்களை` quantity) and `BENCH_CONTENT_TA` (a Kumbakonam
incident whose text also mentions the Madurai High Court bench). Stage 1 derived
those fixture offsets with `source_text.index(quote)`; Stage 2 reproduces them
through the producer and asserts they are identical, offset for offset.

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

Coverage of the twenty-three required Stage 2 span behaviours, all in
`test_spans.py`:

| Behaviour | Test |
| --- | --- |
| English / Tamil / mixed exact span | `test_english_exact_span_has_exact_offsets`, `test_tamil_exact_span_reproduces_the_quote`, `test_mixed_tamil_english_span` |
| Combining vowel signs, not byte offsets | `test_offsets_are_code_points_not_bytes` |
| Newlines inside and around the quote | `test_span_survives_a_newline_inside_the_quote` |
| Quote at start / middle / end | `test_tamil_quote_at_the_very_start_of_the_field`, `test_english_exact_span_has_exact_offsets`, `test_tamil_quote_at_the_very_end_of_the_field` |
| Repeated quote ambiguity + explicit occurrence | `test_repeated_quote_raises_instead_of_choosing_silently`, `test_occurrence_can_be_selected_explicitly`, `test_occurrence_accepts_negative_indexes` |
| Empty / whitespace-only / missing quote rejected | `test_empty_quote_is_rejected`, `test_whitespace_only_quote_is_rejected`, `test_missing_quote_is_rejected`, `test_matching_is_case_sensitive` |
| Correct `char_start` / `char_end` | asserted numerically in each of the above |
| Correct `field_text_hash` | `test_field_hash_is_sha256_of_the_whole_original_field`, `test_hash_covers_the_field_not_the_quote` |
| Verification succeeds | `test_verification_passes_against_the_unchanged_field` |
| Verification after the field changed | `test_verification_detects_a_changed_source_field`, `test_a_mismatch_is_never_repaired` |
| Tamil serialisation survives | `test_tamil_evidence_serialises_without_escaping`, `test_tamil_quote_survives_a_json_round_trip` |
| Record id and source metadata copied | `test_evidence_carries_the_record_and_source_provenance` |
| Method preserved | `test_probabilistic_methods_must_quote_a_confidence`, `test_stage_two_output_matches_the_handbuilt_stage_one_fixture` |
| Confidence preserved | `test_evidence_carries_the_record_and_source_provenance`, `test_exact_match_methods_may_omit_confidence` |
| Original whitespace preserved | `test_surrounding_whitespace_is_preserved_not_stripped`, `test_source_text_is_never_altered_by_the_builder` |

Four real contract gaps were found and closed while writing these tests.
Stage 1: Pydantic's lax float coercion turned the string `"0.9"` into a valid
confidence (fixed with a `BeforeValidator` numeric-type gate in `models/base.py`);
fingerprint ownership was duplicated between `Incident` and `DedupMetadata`
(resolved in favour of `Incident`); and `LanguageInfo` accepted a resolved
`primary_language` with no `detection` record, which is exactly how ingestion's
hardcoded `data.language` would have been laundered into a detection claim.
Stage 2: verifying a span without a recorded field hash would have reported
`validated`, which Stage 1 reserves for hash-backed spans — it now reports
`unvalidated`, because the offsets may be right while the field's identity is
not.

---

## 6. Known limitations

- **No pipeline.** Nothing converts a `CommonRecord` into an `Incident` yet, and
  no text is extracted yet. `tests/builders.py` is hand-written evidence and
  `test_spans.py` quotes strings that were chosen by hand; a real stage picks the
  quotes. Resolving a dotted `field` path against a record object is deliberately
  not implemented — that is the CommonRecord → Intelligence seam, still a later
  stage.
- **Spans are exact-substring only.** No case-insensitive, diacritic-insensitive
  or fuzzy location: a stage that wants one must build a normalised
  representation first and span against the right text, not bend these offsets.
- **A span outlives its field silently.** `verify_evidence` detects drift, but
  nothing calls it yet. Stage 9 persistence and Stage 8 re-clustering are where a
  stored Incident gets re-verified against a re-fetched source, and an offline
  re-verification needs the original field text, which is not stored inside
  `Evidence` by design.
- **Byte-level source drift is all the hash can prove.** `field_text_hash`
  identifies the text, not the record: if a source edits a headline, every
  Evidence over that field reports `mismatch` together, with no per-quote
  attribution beyond the offsets.
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

## 7. External integration dependencies

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

## 8. Next: Stage 3 — the language layer

Stage 3 is the first stage that reads text and decides something about it, and
every decision it makes will be pinned with a span from Stage 2.

Planned modules, inside `intelligence/extraction/`:

- `language.py` — script-ratio detection over code points (Tamil Unicode block
  U+0B80–U+0BFF vs Latin), producing `LanguageDetection` with a `method` and a
  `confidence`. `LanguageInfo.primary_language` now requires an agreeing
  detection record, so this stage is the only legitimate way to resolve a
  language; ingestion's `data.language` goes to `inherited_language_hint` and is
  never trusted.
- `normalise.py` — Unicode normalisation and whitespace control as **new
  `TextRepresentation`s** with `role=normalized` and `derived_from` pointing at
  the source representation. Never in place: the source offsets must stay valid,
  and a normalised variant is exactly what the case- and diacritic-insensitive
  matching that `spans.py` refuses to do will run against.
- `morphology.py` — Tamil case and postposition suffix stripping
  (`-இல்`, `-ஐ`, `-உக்கு`, `-ஆன்`) as a candidate generator. Because bare stems
  are not quotes, a morphology hit produces a normalised-form suggestion that is
  then re-located in the original text and spanned, so the evidence still points
  at the inflected word the article actually printed.
- `boilerplate.py` — removal of the `ADDED :` / `UPDATED :` publish-stamp
  patterns seen in the real Madurai corpus, recorded as a normalised
  representation rather than a mutation, and handed to Stage 4 as the reason a
  timestamp is publication time and not event time.
- `transliteration.py` — Tamil-script English terms (`லேண்ட்`, `கமிஷனர்`) mapped
  to their Latin candidates, keeping both representations.

No keyword rules, no event classification and no severity logic in Stage 3; the
layer only turns record text into trustworthy, language-tagged representations
that later stages can match against. Stage 3 adds no optional dependency: script
detection and suffix stripping are done with Unicode ranges and a small
vocabulary, so the module keeps working with `pydantic` and `pytest` alone.
