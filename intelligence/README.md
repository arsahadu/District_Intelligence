# Intelligence Module

AI / NLP intelligence layer for the Collector's District Intelligence Platform.

Owner: Member 1 (AI / Intelligence). This file is the durable technical record of
the module. It documents only what is implemented **and tested** today, plus the
contract guarantees later stages and other modules rely on.

Current state: **Stages 1-5 complete — contracts, the evidence span primitive,
the language / text representation layer, the temporal extraction layer, and the
`CommonRecord` → `Incident` record seam.** A real Madurai article now maps to a
real `Incident` with not one hand-written byte of evidence. Nothing here reads a
network, a database or a file, and no text is yet classified as an event type,
located from its own sentences, or scored for severity: Stage 5 deliberately
produces a *candidate* whose unresolved sections are the point of it.

```
CommonRecord ──[ Stage 5 seam ]──> Incident (candidate) ──> Evidence / Provenance
                                       │
                     stages 1-4 did the reading underneath it:
                     spans, language and text, temporal roles
                                       │
              Stage 6 places + actors ─┴─ Stage 7 relevance + classification
                                       │
                Stage 8 dedup and clustering ──> Stage 9 persistence ──> GIS
```

---

## 1. What is implemented

**Stage 1** — a versioned, strictly-validated Pydantic v2 data contract for a
structured **Incident** and its **Evidence**, plus the controlled vocabularies
that classify them.

**Stage 2** — `intelligence/extraction/spans.py`: the evidence span primitive
that *produces* that `Evidence`, deriving offsets and hashes from the source
text and verifying them against it later.

**Stage 3** — the text representation layer: `language.py` (script-ratio
detection), `normalize.py` (position-mapped derived text), `morphology.py`
(Tamil suffix candidates), `boilerplate.py` (publish stamps as publication
metadata) and `transliteration.py` (Tamil-script English candidates). Stage 3
decides *what language the text is in and what other spellings of it are legal*;
it still decides nothing about what the text is *about*.

**Stage 4** — the temporal layer, split in two on purpose.
`intelligence/extraction/time_expressions.py` finds and parses temporal
*surfaces* (Tamil and English dates, month-year, numeric dates, clock times,
deictic words, offsets, windows, ranges, weekdays) and resolves relative ones
against a caller-supplied reference; it never attaches a role to anything.
`intelligence/extraction/temporal.py` decides *roles* — which moment a field
establishes as event time, which one it merely printed as publication metadata,
which one is reporting time, which one is retrieval — and assembles Stage 1
`TimeValue`s with `method`, `confidence`, `precision`, `qualifier` and
`evidence_ids`. Stage 4 answers *when the source says it happened*; it still
does not say what happened.

**Stage 5** — `intelligence/mapping/`: the record seam, in two layers that do not
know about each other's business. `record_input.py` is the only file in the
module that knows how a `CommonRecord` is spelled; it reads one structurally by
dotted path, keeps text verbatim, renders non-text values once as metadata, and
never imports ingestion. `assembly.py` knows nothing about `CommonRecord`
spellings: it asks Stage 2, 3 and 4 for every fact and every citation, assembles
the Stage 1 contract, and replays each span against the exact field text it
claims to come from before returning. It names an incident and decides nothing
about it — relevance, event type, severity level, place mentions and the dedup
fingerprint stay unresolved, because Stage 5 holds no evidence for them.

| Package | Purpose |
| --- | --- |
| `intelligence/models/` | Structure: field shapes, types, invariants. |
| `intelligence/config/` | Taxonomy data: event families, department mapping, ordering. |
| `intelligence/extraction/` | Evidence production, text representation and time: spans, hashes, verification, language, normalisation, morphology, stamps, transliteration, temporal surfaces, temporal roles. |
| `intelligence/mapping/` | The record seam: a CommonRecord-shaped input, one cited candidate Incident out. |
| `intelligence/tests/` | Executable specification of every rule below. |

The split is deliberate. `models/` must not change when the district adds an
event category; `config/` must not change when a field gains a validator;
`extraction/` holds the only code allowed to turn "the source says X" into an
`Evidence` object, and `mapping/` may only call it — never build an `Evidence`
itself, which is how a hand-typed offset would get in.
`config.vocabularies.vocabulary_integrity_errors()` returns the drift list when
models and taxonomy disagree, and a test asserts it is empty.

**Stage numbering.** The numbers in this file are this module's build order, not the
hackathon plan's, and they are: 1 contracts, 2 evidence spans, 3 language and text
representation, 4 temporal extraction, 5 the `CommonRecord` → `Incident` record seam,
6 place and actor mentions, 7 relevance and event classification, 8 deduplication and
clustering, 9 persistence and integration, 10 optional LLM intelligence. Stages 1-5
exist; 6-10 are planned and nothing in this module pretends to have done them.

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
                               build_metadata_evidence, compute_field_hash,
                               verify_evidence, revalidate
    language.py                script profiles, LanguageAssessment, detection,
                               LanguageInfo assembly, hint auditing
    normalize.py               MappedText, cluster_end, position-preserving normalize
    morphology.py              SUFFIX_RULES, tokenize, suffix/surface candidates
    boilerplate.py             ADDED/UPDATED stamps, BodySplit, body representation
    transliteration.py         script table, curated vocabulary, Latin candidates
    time_expressions.py        temporal surfaces: find/resolve, precisions,
                               qualifiers, intervals, cues - no semantics
    temporal.py                temporal roles: TemporalMention,
                               TemporalExtraction, extract, TimeValue assembly
    __init__.py                re-exports
  mapping/
    record_input.py            the only CommonRecord-shaped reader: dotted paths,
                               verbatim text, rendered metadata, input_hash
    assembly.py                MappingPolicy, IncidentDraft, map_record,
                               verify_draft, the evidence ledger
    __init__.py                re-exports
  tests/
    conftest.py                fixtures
    builders.py                real-Tamil-content incident factories
    record_fixtures.py         real-shaped CommonRecord factories (Stage 5)
    test_evidence.py                        12 tests
    test_language_and_text.py               16 tests
    test_spatial_severity_time.py           22 tests
    test_classification_and_governance.py   24 tests
    test_incident.py                        15 tests
    test_serialization.py                    8 tests
    test_vocabularies.py                    15 tests
    test_spans.py                           55 tests  (Stage 2)
    test_language_detection.py              21 tests  (Stage 3)
    test_normalization.py                   21 tests  (Stage 3)
    test_morphology_boilerplate.py          34 tests  (Stage 3)
    test_transliteration.py                 26 tests  (Stage 3)
    test_time_expressions.py                90 tests  (Stage 4)
    test_temporal_extraction.py             47 tests  (Stage 4)
    test_record_mapping.py                  52 tests  (Stage 5)
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
confidence with coordinates. `ResolutionState.pending_gis` is what Stage 5 emits for
every record that has a district hint and no place text read, and it stays the expected
output until a location stage exists and GIS is wired. `best_event_location_mention_id` must point
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

**Derived text is a separate object that has to project back.** No Stage 3
function returns a string a caller can span directly: normalisation, stamp
removal and transliteration all return a `MappedText`, which carries the
original range of every character it holds. Evidence still requires a
`SourceField`, and `MappedText.assert_same_field` refuses a field whose text is
not the exact `original` the mapping was derived from, so "we normalised before
spanning" is a crash rather than a quiet bug. A hit found in derived text
becomes a `Span` over the *printed* text after `project()`, which is what lets a
Latin transliteration or a stripped stem cite Tamil source wording.

**A temporal surface is not a temporal fact.** Stage 4 keeps the two halves in
two files. `time_expressions.find()` returns `Expression`s — text, offsets,
precision, qualifier, arithmetic — and no `TimeSemantics`; the test
`test_semantics_is_never_claimed_by_the_pattern_layer` proves the module has no
vocabulary for a role at all. `temporal.py` is the only place that says whether a
moment is the event, the publication, the reporting or the retrieval. Splitting
them means a parser cannot quietly promote the header it matched to an event
time, and a role rule can be changed without touching a pattern.

**A printed stamp is structurally incapable of becoming event time.** Matching
runs on `boilerplate.split(field).body`, and the body is a `MappedText` built by
*dropping* the stamp ranges from the original. So `event_time()` cannot find a
stamp even if the whole stamp-matching vocabulary were wrong: the text it would
have to match is not in the corpus the rules run over. Where `boilerplate`
isolates only the label, because the timestamp that follows it is a shape its
grammar does not parse, Stage 4 demotes whatever else sits on that stamp's line
to `publication_time` — see §6. `publication_time()` is the only accessor that
reads stamps, and `event_time()` records `STAMP_ONLY` when a field has a stamp
and no body time — the abstention is documented in the value, not left to the
caller to infer.

**Relative expressions resolve only against a reference the caller supplies.**
`நேற்று` with no `reference` produces `resolved=False`,
`method=unresolved`, `value=None`, `needs_reference=True` and a reason naming the
missing input; with `reference=datetime(2026, 10, 2, 9)` the same surface
produces `2026-10-01T00:00` at `day` precision. Nothing in the module reads a
clock, so "today" can never silently mean the day the test ran.
`reporting_cue()` works the same way: a cue makes the *reported* moment distinct
from the moment the article was retrieved, and the retrieval time is only a fact
when the caller passes `retrieved_at`.

**Precision declares what the source actually said.** Every resolved value is
the *earliest instant* of the matched period — month precision yields the 1st at
00:00, year precision yields Jan 1 — and `precision` is set from the surface, not
from the arithmetic, so `அக் 2026` is `2026-10-01T00:00` at **month** precision
with `qualifier=approximate`. It never pretends to a day it did not read, and a
reviewer who needs the real period has the precision to reconstruct it. A range
or a window is different again: `is_interval=True` and the endpoints are separate
`Expression`s with their own spans, so `ஜூலை 20 முதல் 25 வரை` reports an
interval with **no instant at all** rather than picking a side.

**Ambiguity is reported, never resolved by silently picking.** `02.10.2026` is
read day-first — the district's convention — but the surface keeps
`ambiguous=True` and the mention's confidence drops to `0.6`, so a reviewer sees
that `2026-10-02` was a choice. `28.09.2026` is *not* flagged, because 28 cannot
be a month and only one reading exists. Where no reading is safe nothing is
claimed: `ஜூ 2026` abbreviates two months identically, so it stays
unresolved with `AMBIGUOUS_ABBREVIATION`, and a bare `2 ஆம் தேதி` is
unresolved with `NO_MONTH`. Every one of those reason strings is carried into the
`TimeValue.notes` the reviewer reads.

**Time zone is configuration, never inference.** Precedence is the caller's
`timezone` argument, then a zone spelled in the text (`timezone_cue()`), then
`None`. Values stay naive: the module does not attach an offset it got from
`zoneinfo`, because a Tamil district report's times are only comparable to each
other, not to a UTC log. `TimeValue.timezone` records the name that was declared,
and an undeclared naive value is exactly what `inconsistencies()` reports.

**A record's own claim is data with a citation, never a fact with authority.**
Stage 5 reads every scalar a producer asserts — `event_time`, `severity`, `status`,
`location.district`, `data.language` — and each one lands in the incident either as a
quoted span or as an enum member that says the module did not decide it. Nothing in
that set can raise a confidence, promote a status, or fill a slot a stage has not
earned. The corollary is the text rule: a value is body text only if the policy named
its path, because a rendered `data.station_id` is not a sentence and must not join the
vote over which language a record is written in.

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

Stage 1 defined the shape; Stage 5 reads it. `mapping/record_input.py::read_record()`
takes any object that satisfies those dotted paths — a Pydantic instance, a plain
dict, a fixture — and resolves `data.content` and `location.district` as paths, not
as imports. Nothing under `intelligence/` names the `ingestion` package, so the
module stays testable with ingestion absent, and a field rename in ingestion surfaces
as a `RecordShapeError` at the seam rather than as a broken import at collection time.
The `Evidence` field names mirror the record one-to-one, which is what keeps the
mapping mechanical. Two deliberate non-assumptions, both now implemented behaviour
rather than intentions:

- `CommonRecord.data["language"]` is copied to
  `LanguageInfo.inherited_language_hint` for audit and *not* trusted as the
  primary language — ingestion hardcodes it. `read_record()` refuses to treat
  `data.language` as body text at all, so it cannot reach the detector that would
  "confirm" the hint, and Stage 1's contract rejects a resolved `primary_language`
  with no `LanguageDetection` behind it, so the inherited value has nowhere to leak
  into.
- `CommonRecord.location.district` maps to
  `SpatialHint.district_hint` with `district_hint_authority=source_configuration`,
  never to an event location. It is recorded as an article-independent hint and
  `inconsistencies()` says so in plain language.

Everything else the record holds is preserved, not summarised: `source_id`,
`source_type`, `record_id`, `source_url`, `raw_reference` and `retrieved_at` are copied
onto every `Evidence` the seam produces, `record_id` also reaches
`Incident.supporting_record_ids`, and text values are stored exactly
as the record holds them — the site navigation prefix, the duplicated
`UPDATED : … ADDED : …` stamps and the press-style ‘‘ … ’’ quotation marks are all
still there for the extractors to contend with. Values outside the declared text keys are metadata
even when they are strings: a `data.warning` or a `data.station_id` is rendered once so
evidence can cite it, and it is never a body a span may be cut from. The record's own
scalars — `severity`, `status`, `event_time` — are quoted as fields holding exactly
what the producer asserted, which is how a claim can be preserved without being
adopted.

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
incident, whereas the dedup decision is metadata *about* it. Stage 5 leaves
`fingerprint` as `None` and `dedup` as the contract's default abstention
(`decision=unresolved`, `method=unresolved`, no `algorithm_version`, no `cluster_id`):
computing a fingerprint is a deduplication decision, and a candidate that carries one
has already claimed it might be a duplicate of something it has never been compared
to.

### Vocabulary contract

`config/vocabularies.py` — `TAXONOMY_VERSION = "2026.10-stage1"`:

- 70 `EventType` codes partitioned into 15 families
  (`EVENT_TYPE_FAMILIES`), each mapped to owning departments
  (`EVENT_TYPE_DEPARTMENTS`) over 33 `Department` codes.
- Event codes are ASCII snake_case domain concepts with no language tokens, so
  a Tamil and an English article about the same incident map to one code.
- `INFORMATIONAL_EVENT_TYPES` isolates ceremony / announcement / transfer /
  sports coverage — newsworthy, not district incidents. This is the
  Stage 7 relevance prior.
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

This layer still takes field text from its caller and does not read, resolve or
import a `CommonRecord` — that is Stage 5's job (§7), and `mapping/` calls these
builders instead of reaching around them to touch a record.

---

## 5. Stage 3 — language and text representations

```
SourceField (untouched field text - the only thing a span may be cut from)
   │
   ├── language.assess(text) ──────────> LanguageAssessment
   │        script ratios over letters     │ .detection()  -> LanguageDetection
   │                                       │               (method, confidence, ratios)
   │                                       └ .language_info(...) -> LanguageInfo
   │
   ├── normalize.normalize_field ──> MappedText ─┐
   ├── boilerplate.split ──────────> MappedText ─┤ each .to_representation()
   └── transliteration.transliterate > MappedText ┘ -> TextRepresentation
                                                 (derived_from + role + provider)
        morphology.tokenize / suffix_candidates / surface_forms
            -> candidate stems and printed forms, each locatable in the source
```

`MappedText` is deliberately not a `SourceField` and has no `record_id`: it can
produce a `Span`, but that span only becomes `Evidence` once it is handed back
to the field it came from.

### Language detection

Detection reads code points, never `CommonRecord.data["language"]`. Letters are
bucketed into `tamil` (U+0B80–U+0BFF), `latin` (ASCII letters plus Latin-1
Extended-A and Latin Extended Additional, which covers transliterated press
text) or `other`; marks, digits and punctuation are excluded because they say
nothing about a language.

| Condition over letter ratios | `primary` | `secondary` | `is_multilingual` |
| --- | --- | --- | --- |
| Tamil ≥ 0.8 | `ta` | `en` when Latin ≥ 0.1 *and* an English function word is present | only with that English evidence |
| Latin ≥ 0.8 with English function words | `en` | `ta` when Tamil ≥ 0.1 | as in the Tamil row |
| Latin ≥ 0.8, no English function words | `und` | — | False |
| Tamil ≥ 0.1, Latin ≥ 0.1, neither ≥ 0.8, English corroborated | `mul` | — | True |
| Any script ≥ 0.8 that is neither Tamil nor Latin | `und` | — | False |
| No letters at all | `und` | — | False |

`confidence` is the dominant script ratio (`1 - |tamil - latin|` for `mul`,
`None` for `und`), so a mixed article is honestly less confident than a monolingual
one, and a declined detection claims nothing. `reason` states the rule in words.
The thresholds are named constants (`DOMINANT_SCRIPT_RATIO`,
`PRESENT_SCRIPT_RATIO`).

`language_info(sources, derived_representations=…)` assembles the Stage 1
contract: one `source` representation per field (auto-id
`rep-{record_id}-{field-path}`), the derived ones the caller adds, and a
`LanguageDetection` aggregated over the fields — per record, the field with the
most letters decides that record's language, and `detected_by_record` keeps the
per-record map visible. Passing a `source`-role representation as *derived*
raises instead of duplicating the source text.

`normalise_hint()` maps a feed label (`"Tamil"`, `"en-IN"`) to a code and
`hint_conflict()` reports a disagreement. The hint is an audit trail only; it
never enters `assess()`.

### Normalization

`normalize(text, unicode_form="NFC", lowercase=True, collapse_whitespace=True,
drop_ranges=())` produces a `MappedText`. Three properties matter:

- Grapheme clusters are walked by Unicode *category* (`Mn`, `Mc`, `Me`), not by
  `unicodedata.combining()`: Tamil dependent vowel signs are spacing marks with
  combining class 0, so a class-based scan splits syllables in half.
- Whitespace runs collapse to a single space and are never stripped, so leading
  and trailing whitespace keeps a position a caller can project.
- The steps applied are recorded in `steps` (`unicode:NFC`, `lowercase`,
  `collapse_whitespace`, `drop:N`), and `original` is never mutated.

Because a `MappedText` re-locates everything through `project()`, a case- or
diacritic-insensitive match runs on the normalised text while the `Evidence`
still quotes the original — the exact behaviour `spans.py` refuses to fake.

### Tamil morphology

`tokenize()` splits on anything that is not a letter, mark or digit and returns
`Span`s, so a token is citable where it was printed. `suffix_candidates(word)`
offers every reading of a word as stem + one suffix from `SUFFIX_RULES`
(longest surface first, vowel-sign spellings only: `-இல்` prints as `ில்`),
including restorations for the two sandhis the corpus shows — a dropped final
`ம்` (`அலுவலகம்` → `அலுவலகத்தில்`) and a dropped `்` (`கமிஷனர்` →
`கமிஷனரிடம்`). `surface_forms(stem)` runs the same rules forward so a dictionary
stem can be checked against a printed word.

It is a candidate generator: every candidate carries the printed `form`, nothing
is asserted to *be* a stem, plural-then-case inflection is read one suffix at a
time, and an unmodelled junction (e.g. `வழக்கு` → `வழக்கில்`) returns no match
rather than a near miss.

### Boilerplate

`find_boilerplate()` matches `ADDED` / `UPDATED` / `PUBLISHED` plus an optional
Tamil or Latin month-date and clock time, in either case, with ASCII or
full-width colons. `Boilerplate` stores the printed text and offsets plus the
timestamp text — **no `datetime`**, because parsing it would be an
interpretation — and its `semantics` property is pinned to
`TimeSemantics.PUBLICATION_TIME`. `split(field)` returns a `BodySplit`: the
stamps, and a body `MappedText` built by dropping the stamp ranges from the
original, so body offsets still point at the untouched field.
`timestamp_evidence()` pins each printed stamp as `regex` Evidence whose note
says "not event time". Stage 4 gets the publication time as separate, cited
metadata; the body it reads has no stamp in it.

### Transliteration

`transliterate()` renders Tamil clusters to Latin through a fixed table
(`க`→`ka`, `க்`→`k`, `ை`→`ai`, `ழ`→`zh`), leaving anything outside the table
alone; the result is a `MappedText`, so every Latin character knows the Tamil
range it came from. `candidates(word)` returns the curated vocabulary spelling
first (`method=dictionary`), then a stem's vocabulary spelling for an inflected
word (`basis="vocabulary of stem"`), then the raw table rendering
(`basis="table", method=rule`) — deduplicated, each carrying the printed Tamil
`form`. Latin input yields no candidate; there is no machine translation and no
meaning attached to any entry. `representation()` emits a `transliterated`
representation whose `language` stays the source language, because a
letter-for-letter rendering is not a translation.

The vocabulary is ten entries: loanword spellings the press already writes
phonetically (`கமிஷனர்`→Commissioner, `லேண்ட்`→Land), place names
(`மதுரை`→Madurai, `கும்பகோணம்`→Kumbakonam, `சென்னை`→Chennai,
`தஞ்சாவூர்`→Thanjavur, `சிவகங்கை`→Sivaganga) and one press spelling
(`மாநகராட்சி`→Managaram). Every entry carries a `note` naming its kind so the
list is auditable.

### Stage 3 behaviours, and where each is pinned

| Required behaviour | Test |
| --- | --- |
| Tamil text detected | `test_tamil_body_is_detected_as_tamil`, `test_tamil_detection_carries_a_method_and_script_ratios` |
| English text detected | `test_english_prose_is_detected_as_english`, `test_english_vocabulary_signal_uses_function_words_only` |
| Mixed / code-switched text | `test_tamil_dominant_text_with_english_words_names_both_languages`, `test_code_switched_text_with_no_dominant_script_is_multilingual`, `test_two_records_are_detected_separately_and_together` |
| Unknown and other-script text | `test_text_without_letters_is_unknown`, `test_other_scripts_are_neither_tamil_nor_english`, `test_latin_script_alone_does_not_claim_english`, `test_only_letters_count_as_script_evidence` |
| Source metadata never trusted as detection | `test_inherited_hint_is_recorded_but_never_decides_the_language`, `test_hint_without_detection_is_still_reported_as_a_gap`, `test_hint_labels_are_normalised_to_codes`, `test_agreeing_hint_produces_no_conflict` |
| Normalization leaves the original intact | `test_original_text_is_never_replaced`, `test_whitespace_runs_collapse_without_being_stripped`, `test_normalization_records_what_it_did`, `test_mapped_text_is_a_value_not_a_mutating_object` |
| `derived_from` relationship | `test_normalized_representation_names_its_parent`, `test_a_derived_representation_without_a_parent_is_refused`, `test_source_representation_is_verbatim_and_declares_its_method` |
| Findings map back to original offsets | `test_a_term_found_in_derived_text_maps_back_onto_the_original`, `test_evidence_from_derived_text_validates_against_the_original`, `test_derived_offsets_cannot_be_cited_against_a_different_field`, `test_body_offsets_point_at_the_untouched_original` |
| Tamil suffix candidates | `test_locative_suffix_resolves_to_the_dictionary_stem`, `test_plural_and_accusative_suffixes`, `test_a_dropped_ampul_is_offered_back_as_the_stem`, `test_a_dropped_pulli_is_offered_back_as_the_stem` |
| Suffix handling with combining characters | `test_tamil_combining_marks_are_never_split`, `test_suffix_arithmetic_uses_code_points_not_bytes_or_glyphs`, `test_suffix_surfaces_are_spelled_with_vowel_signs`, `test_a_decomposed_accent_composes_without_losing_source_width` |
| Morphology never over-claims | `test_the_longest_suffix_is_offered_first`, `test_plural_then_case_is_only_read_in_one_step`, `test_an_unmodelled_junction_is_a_miss_not_a_guess`, `test_a_fragment_is_not_reported_as_a_stem` |
| ADDED / UPDATED detection | `test_both_stamps_are_found_in_the_real_fixture`, `test_stamp_timestamps_are_pinned_separately_from_the_label`, `test_full_month_without_a_time_is_still_a_stamp`, `test_a_label_without_a_timestamp_is_still_isolated` |
| Publication metadata separated from event semantics | `test_stamps_are_publication_metadata_and_never_event_time`, `test_stamp_evidence_validates_and_says_what_it_is_not`, `test_a_boilerplate_record_holds_printing_not_interpretation`, `test_the_body_is_a_new_representation_not_a_replacement` |
| Transliteration candidates | `test_a_loanword_keeps_the_latin_spelling_it_came_from`, `test_a_suffix_keeps_the_stem_spelling_visible`, `test_place_names_from_the_real_fixtures`, `test_text_that_is_already_latin_produces_no_candidate` |
| Original Tamil preserved | `test_the_printed_tamil_form_is_carried_by_every_candidate`, `test_transliteration_never_touches_the_original`, `test_the_original_tamil_survives_alongside_the_latin_reading`, `test_a_field_transliterates_without_losing_a_character` |
| Real fixture text | `test_analyse_tokens_covers_a_real_fixture`, `test_candidates_for_text_covers_the_fixture_and_stays_locatable`, `test_a_candidate_is_only_citable_through_its_printed_form` |
| Serialization through Stage 1 contracts | `test_output_validates_through_the_stage_one_contract`, `test_contracts_refuse_invented_fields`, `test_body_representation_plugs_into_the_language_contract`, `test_normalized_representation_plugs_into_the_language_contract` |
| Derived text cannot be cited as source | `test_evidence_refuses_a_word_mapped_from_a_slice_instead_of_the_field`, `test_pieces_that_reorder_the_source_are_refused`, `test_language_info_refuses_a_source_representation_it_would_duplicate` |

---

## 6. Stage 4 — temporal intelligence

```
SourceField (untouched field text, optional retrieved_at)
   │
   ├── boilerplate.split(lowercase=True) ──> BodySplit { items: stamps, body: MappedText }
   │        the stamp text is dropped from the body, so it cannot be matched by a
   │        time rule at all; every body offset still points at the original field
   │
   ├── body ──> time_expressions.find() ──> [Expression]     surface + arithmetic only
   │              18 rules, 10 kinds, longest-phrase-first, one surface per phrase
   │              └── resolve(reference?) ──> Resolution {value, precision, qualifier,
   │                                       resolved, ambiguous, reason, notes}
   │
   ├── body.project(start, end) ──> Span in ORIGINAL coordinates
   │        └── body.evidence_at(source, span, method, confidence, notes) ──> Evidence
   │
   └── temporal.extract(source, reference=…, timezone=…) ──> TemporalExtraction
             mentions (event_time | reported_time) + publication (stamps)
             + retrieval (record metadata), each with its own Evidence
```

### Surfaces: `time_expressions.py`

`find(text)` runs every rule over the whole text, then `_resolve_overlaps()`
keeps one surface per phrase: longest match first, then earliest start, then the
rule table's own precedence. `resolve(expression, reference=None)` turns one
surface into a partially-known instant or explains why it cannot, and never
raises — an unusable surface comes back `resolved=False` with a `reason`.

| Kind | Rules | What the text has to say |
| --- | --- | --- |
| `absolute_date` | ISO, `28 செப்டம்பர் 2026`, `செப்டம்பர் 28, 2026`, `28.09.2026` / `28/9/2026` / `28-9-2026` | a day, a month and a year within `MIN_YEAR..MAX_YEAR` |
| `month_day` | `அக் 2`, `2 ஆம் தேதி`, `Oct 2` | day and month, no year — resolved is *refused*, reason `NO_YEAR` / `NO_MONTH` |
| `month_year` | `அக் 2026`, `October 2026` | a period, so precision `month` |
| `year_only` | `2026 ஆம் ஆண்டு` | a bare `2026` is not a date; the marker is required |
| `clock_time` | `10:45`, `4:30 PM`, `10 மணி`, `10.45 மணி`, `இரவு 10 மணிக்கு` | a time of day with no calendar — `NO_DATE` until a day holds it |
| `relative_day` | `நேற்று`, `இன்று`, `நாளை`, `நேற்று முன்தினம்`, `yesterday`, `the day after tomorrow`, plus 15 fused forms (`நேற்றிரவு`, `tonight`) | an offset from the reference; a day part narrows it without naming an hour |
| `relative_offset` | `2 மணி நேரம் முன்பு`, `3 days later`, `in two days`, `இரண்டு நாட்களுக்குப் பிறகு`, `ஒரு வாரத்திற்கு அடுத்து` | a counted shift; a bare duration with no marker is rejected |
| `relative_window` | `கடந்த இரண்டு நாட்களாக`, `last 3 days`, `this month` | an interval whose value is its start, `qualifier=ongoing` |
| `weekday` | `திங்கள்`, `திங்கட்கிழமை`, `next Friday`, `வெள்ளியன்று` | a name, not a day: always unresolved (`NO_ANCHOR`) unless the text anchors it |
| `date_range` | `ஜூலை 20 முதல் 25 வரை`, `January 5, 2026 to January 10, 2026` | an interval with two endpoint `Expression`s, each with its own span; the opening endpoint has to be a readable date on its own, so `20 முதல் 22 வரை` yields only `22 வரை` |

Matching is done on the normalised body, so `செப்டம்பர்`, `செப்.` and
`செப்` are one month; the hit is projected back for citation. Tamil word
boundaries cannot use `\b` — a dependent vowel sign is a spacing mark (`Mc`), so
a word boundary lands *inside* a declined word — and every rule is wrapped in
`_LEAD` / `_TRAIL` code-point class guards instead. Direction words
(`முன்பு`, `பிறகு`, `ago`, `later`) are kept on the surface rather than folded
into the arithmetic, so a reviewer can see what the text said. A Tamil dative unit
puts an explicit euphonic consonant before a forward marker — `நாட்களுக்குப்
பிறகு`, not `நாட்களுக்கு பிறகு` — so `_OFFSET` accepts an optional `ப்|க்|ட்|ய்`
between the unit and the marker, which is what makes the forward count readable
while `பிறகு அவர் பேசினார்` ("then he spoke") stays a non-surface. A colon clock that
begins right after a sign is refused, because the `+05:30` of an ISO timestamp is
a zone offset and not a time of day.

`timezone_cue(text)` reads a zone only when the text names one
(`இந்திய நேரப்படி`, `IST`, `Indian Standard Time`); `reporting_cue(text, start,
end)` returns the attributing verb only when one is within
`REPORTING_CUE_WINDOW = 90` characters in the *same sentence*
(`SENTENCE_BREAKS` stops it reaching into the next one). Both return `None`
rather than a guess, and both are read from vocabularies that a drift test
(`test_the_boilerplate_month_names_are_read_by_the_time_rules_too`) keeps in step
with `boilerplate.py`'s month names.

### Roles: `temporal.py`

`extract(source, *, reference=None, timezone=None, split=None)` returns a
`TemporalExtraction`, which is the only Stage 4 object a later stage should
touch. It refuses a `split` derived from a different field (`TemporalError`), and
re-uses one the caller already built rather than normalising twice.

| Role | Produced from | Guarantee |
| --- | --- | --- |
| `event_time` | a body surface with no reporting cue | `event_time()` never falls back to a stamp; with no body time it returns `unresolved_time()` carrying `NO_EVENT_TIME` and, when a stamp exists, `STAMP_ONLY` |
| `reported_time` | a body surface with a same-sentence reporting cue | kept in a separate accessor, so "the police said it happened on the 28th" is not quietly the incident's timestamp |
| `publication_time` | a `boilerplate` stamp's printed timestamp | `STAMP_SEMANTICS` is a module constant pinned to `publication_time`; `_stamp_note()` writes "…never event time" into the Evidence itself; the value is the *latest* parseable stamp |
| `retrieval_time` | `SourceField.retrieved_at` | `method=source_metadata`, `confidence=1.0`, field-level Evidence (`span_validation=not_applicable`, no quote) — it is a record fact, not a text fact |
| unresolved | anything the text does not settle | `value=None`, `method=unresolved`, `semantics=unknown`, `confidence=None`, reason in `notes` |

`boilerplate` isolates a stamp *label* even when its timestamp is a shape its own
grammar does not parse, so `ADDED : 2026-10-02 09:46` used to leave that bare
timestamp in the body — where a time rule found it and, being minute-precise,
**won the event slot**. That is the exact failure requirement 4 forbids, so
`extract()` now treats the rest of a stamp's own line as publication material:
`_stamp_residue()` takes the original text from the label to the next newline,
any body surface inside it is demoted to `publication_time` with the note
"publication metadata printed in a shape the stamp grammar does not parse, never
event time", and the line boundary means `ADDED : …` cannot swallow the article
text below it. The residue rule is what makes the invariant hold for stamp shapes
nobody has seen yet; widening `boilerplate`'s grammar would only have moved the
hole.

`TemporalMention` is the per-surface record: `kind / rule / raw_text /
char_start / char_end / precision / qualifier / value / resolved / reason /
ambiguous / needs_reference / is_interval / notes / method / confidence /
evidence_ids`, plus `interval()` (the two endpoint instants of a range) and
`time_value()` — the Stage 1 `TimeValue`, which is what Stage 5 puts in
`Incident.event_time` after checking it against the record's own `event_time` (§7).
`as_dict()` on the extraction serialises the roles and the
provenance as separate blocks, including `stamps` straight from
`split.stamps_as_dicts()`, so a stamp can always be told apart from a mention.

Method and confidence are mechanical, not judged: a matched calendar surface is
`regex`, a surface whose meaning needs arithmetic against a reference is `rule`,
and an unresolved mention carries `unresolved` with **no** confidence at all.
Resolved values read `0.95` with a clock, `0.9` for a date, `0.85` for a
month/year period, `0.8` for a relative expression, `0.6` when the surface could
be read two ways, and `1.0` only for record metadata.

### Evidence discipline in Stage 4

Every mention is cited through `body.evidence_at(source, body.project(...))`, so
offsets are *projected*, never typed: the pattern layer works in normalised
coordinates and the `Evidence` quotes the untouched field. Stamps are cited with
`build_evidence_at()` over `item.timestamp_span()`. Retrieval time uses the new
`build_metadata_evidence()` in `spans.py`, which is the one legal way to produce
`method=source_metadata` Evidence without a quote — it derives the evidence id
and the `field_text_hash` from the field and leaves `span_validation` at
`not_applicable`. `test_every_time_value_points_at_evidence_that_exists` walks
every emitted `TimeValue` and checks each `evidence_id` resolves, re-verifies
against the source, and quotes exactly its own `raw_text`.

### Stage 4 behaviours, and where each is pinned

| Required behaviour | Test |
| --- | --- |
| Tamil and English named dates | `test_named_dates_are_read_in_both_orders_and_both_scripts`, `test_a_named_date_is_whole_even_when_both_orders_could_read_it` |
| Numeric dates, both readings | `test_numeric_dates_are_read_day_first_and_flagged`, `test_a_numeric_date_that_cannot_be_day_first_is_read_the_other_way`, `test_only_one_surface_is_cut_from_a_numeric_date` |
| ISO dates and impossible calendars | `test_an_iso_date_is_read_whole`, `test_an_impossible_calendar_date_is_refused_not_repaired`, `test_numbers_that_are_not_dates_produce_nothing` |
| Month names and month-year | `test_a_month_and_year_keeps_the_month_as_the_smallest_true_unit`, `test_an_abbreviation_that_names_two_months_claims_neither`, `test_the_boilerplate_month_names_are_read_by_the_time_rules_too` |
| Year-only and day-only | `test_a_year_alone_never_becomes_a_day`, `test_a_day_of_the_month_is_only_a_day_when_the_text_marks_it`, `test_a_day_and_month_without_a_year_stays_unresolved` |
| Explicit times, with and without a meridiem | `test_a_clock_reading_is_kept_from_the_calendar`, `test_a_meridiem_supplies_the_hour_that_the_clock_lacks`, `test_a_clock_without_a_meridiem_says_so`, `test_an_iso_zone_offset_is_not_a_clock_reading` |
| Date + clock as one phrase | `test_a_day_part_and_a_clock_are_one_phrase_not_two`, `test_an_english_date_and_time_is_one_surface`, `test_a_period_does_not_join_a_date_and_a_clock_across_a_sentence` |
| Tamil and English relative expressions | `test_relative_days_are_counted_from_the_reference`, `test_a_fused_day_part_keeps_the_day_approximate`, `test_a_relative_day_and_a_clock_are_one_phrase`, `test_now_is_today_and_carries_the_clock_of_the_reference` |
| Relative stays relative without a reference | `test_a_day_word_without_a_date_still_needs_a_reference`, `test_a_relative_expression_stays_relative_without_a_reference`, `test_the_same_relative_expression_resolves_once_a_reference_is_given`, `test_relative_arithmetic_uses_the_reference_it_was_given` |
| Offsets, ongoing windows, ranges | `test_hour_offsets_move_the_clock_of_the_reference`, `test_a_tamil_forward_offset_is_read_through_its_euphonic_consonant`, `test_a_direction_word_alone_is_not_an_offset`, `test_a_duration_without_a_marker_is_not_a_point_in_time`, `test_an_ongoing_window_is_reported_as_its_start`, `test_a_range_is_an_interval_and_claims_no_instant`, `test_a_range_gives_its_endpoints_their_own_spans` |
| Weekdays and their ambiguity | `test_a_weekday_name_is_never_one_specific_day`, `test_a_fused_tamil_weekday_adverb_is_still_a_weekday`, `test_a_full_tamil_weekday_is_not_treated_as_ambiguous`, `test_an_english_weekday_is_not_read_by_the_tamil_rule`, `test_weekdays_also_make_a_range` |
| Mixed Tamil / English text | `test_named_dates_are_read_in_both_orders_and_both_scripts`, `test_an_english_weekday_is_not_read_by_the_tamil_rule`, `test_the_finest_true_reading_wins_the_event_slot`, `test_tamil_printing_survives_the_whole_extraction` |
| Publication and update stamps are never event time | `test_the_stamp_fixture_carries_no_event_time`, `test_stamps_are_publication_time_and_never_anything_else`, `test_the_latest_stamp_is_the_publication_time`, `test_stamp_text_never_reaches_the_body_mentions`, `test_a_field_without_stamps_says_so_instead_of_inventing_one`, `test_a_timestamp_the_stamp_grammar_misses_is_still_not_event_time`, `test_a_stamp_timestamp_that_could_be_read_two_ways_keeps_the_publication_role`, `test_a_stamp_line_owns_only_its_own_line`, `test_a_reused_split_still_demotes_its_stamp_lines` |
| Reporting time separated from event time | `test_a_statement_attributed_to_a_reporting_verb_is_not_the_event`, `test_event_and_reported_times_are_kept_apart_in_one_field`, `test_a_reporting_verb_in_the_same_sentence_is_found_next_to_a_surface`, `test_a_cue_in_the_next_sentence_does_not_reach_back` |
| Retrieval time as a separate role | `test_the_retrieval_clock_is_its_own_role`, `test_a_field_without_a_retrieval_clock_has_no_retrieval_time` |
| Precision and qualifier preserved | `test_the_finest_true_reading_wins_the_event_slot`, `test_a_day_part_narrows_the_day_without_inventing_an_hour`, `test_an_hour_only_clock_is_less_precise_than_a_minute_clock`, `test_a_clock_without_a_date_is_left_unplaced` |
| Ambiguous expressions flagged | `test_a_numeric_date_that_could_be_read_both_ways_is_flagged`, `test_a_numeric_date_with_only_one_possible_reading_is_not_flagged`, `test_an_abbreviation_that_names_two_months_claims_neither` |
| Evidence offsets and quote preservation | `test_every_mention_quotes_the_untouched_field_at_original_offsets`, `test_evidence_is_one_per_surface_and_unique`, `test_every_time_value_points_at_evidence_that_exists`, `test_a_demoted_stamp_line_is_still_cited_in_the_untouched_field` |
| Normalised match, original offsets cited | `test_a_body_match_is_cited_in_original_coordinates_after_normalization`, `test_the_original_field_is_never_replaced_by_the_body` |
| Unresolved claims nothing | `test_an_unresolved_value_never_claims_a_method_or_confidence`, `test_unresolved_time_is_the_honest_default`, `test_a_field_that_says_nothing_about_time_is_recorded_as_silent` |
| Time zone declared, not inferred | `test_a_declared_zone_is_attached_without_touching_the_naive_value`, `test_a_zone_named_in_the_text_is_read_when_the_caller_does_not_declare_one`, `test_a_zone_the_caller_declares_beats_the_one_the_text_names`, `test_a_time_zone_nobody_declared_stays_undeclared` |
| Contracts stay language-independent | `test_semantics_is_never_claimed_by_the_pattern_layer`, `test_the_serialised_shape_keeps_roles_and_provenance_apart`, `test_extraction_is_deterministic` |

Stage 4 adds no dependency and no LLM call: surfaces are `re` over Unicode code
points, arithmetic is `datetime`, and the only new standard-library import is
`timedelta`.

---

## 7. Stage 5 — the record seam

`intelligence/mapping/` is where a `CommonRecord` becomes an `Incident`. Two files,
and the split between them is the whole design:

```
record_input.py    knows how a CommonRecord is spelled. Nothing else in the module does.
        │  RecordInput: provenance, body text untouched, non-text values rendered once
        ▼
assembly.py        knows what an Incident needs. Knows no field name of any record.
        │  asks Stage 2 for every span, Stage 3 for language and stamps, Stage 4 for time
        ▼
IncidentDraft(incident, record, fields, split, temporal, checks, warnings)
```

`map_record()` accepts either a `RecordInput` or anything shaped like a record; given
the latter it reads it with the policy's own `text_keys` / `extra_keys`. Everything a
record states but an `Incident` has no slot for is still kept: cited into the evidence
ledger and said out loud in `processing.warnings`, so nothing quietly disappears.

**Only two things can become body text.** The title and a path listed in `text_keys`.
`data.station_id` is `"MDU"`, a perfectly readable string, and a string reached
through `extra_keys` is rendered and quoted as metadata — it never enters the text the
language detector aggregates or the time rules scan. This was a real defect caught by
the Stage 5 tests: the first version treated *any* string the policy named as a source
of text, so a record's short metadata strings joined the letter-count vote over its
Tamil body. "A rendered value is never a body a span may be cut from" is now a set
membership test inside `read_record`, not a convention in a comment.

**Time: the record's own answer is an input, not a fact.** `CommonRecord.event_time`
is read, preserved and — for a news article — *not* used as event time, because for
this feed it is the `ADDED : …` publish stamp captured from the page (§10). The order
is stated once, in `_event_time()`: a `record_type` listed in
`time_trusted_record_types` and holding a parseable datetime wins with
`method=source_metadata`, `qualifier=exact` and its own ISO string cited as the span;
otherwise a warning records the rejection and Stage 4's `event_time()` over the stamp-free
body is consulted; if the text says nothing, `publication_time()` stands in with
`semantics=publication_time` and a warning that says the article never said when; if
even that is absent, the `TimeValue` comes back with `value=None`,
`method=unresolved` and Stage 4's note for an absence — `inconsistencies()` then
says "no time established at all", which is the honest answer rather than a padded
one. `retrieved_at` is the reference clock (`policy.reference or record.retrieved_at`),
so the temple-closure article's `நேற்று முன்தினம் இரவு 7:45 மணிக்கு` resolves to
`2026-10-01T19:45` against the capture's own timestamp and shifts correctly when the
capture date moves, while a bare `சனி` in the events listing still resolves to nothing.
`Asia/Kolkata` is the *declared* zone in `MappingPolicy`, overridable per feed and
hashed into `config_hash()`; a naive `retrieved_at` is reported, never converted.

**Place, severity and status: preserved, never translated.** `location.district`
becomes `SpatialHint.district_hint` with `district_hint_authority=source_configuration`
and the raw string quoted from `location.district`; `resolution_state` is
`PENDING_GIS`, `mentions` stay empty, and `best_event_location_mention_id` is unset
because Stage 6 has not read a place phrase yet. A record asserting no district gets
`NOT_ATTEMPTED` rather than a hint of `None`. A source severity of `"Orange Alert"`
produces `Severity(level=UNRESOLVED, …)` whose `evidence_ids` cite the string: the
level is not claimed, the words are not lost, and `severity_rank()` still refuses to
rank it. `status="UPDATED"` is quoted and warned about; `Incident.status` is the
Intelligence workflow state, and Stage 5 only ever sets `CANDIDATE`.

**Evidence: nothing is quoted twice and nothing is typed.** A `_Ledger` holds one
`SourceField` per dotted path and refuses a second `read_text` of the same path with
different text — one record holds one version of a field. Each field gets one
`source_metadata` evidence covering the whole value; every quote comes from
`SourceField.evidence()` or Stage 4's `evidence_at()`, and no `Evidence(...)` is
constructed anywhere in `mapping/` — that absence is the invariant, and it is what
makes a hand-typed offset impossible rather than discouraged. A scalar quoted as
metadata (`severity`, `status`, `data.language`, `event_time`) becomes a field whose
text *is* that rendered value, so its span covers the whole field and
`verify_evidence` still means something. `verify_draft()` then replays the *entire*
ledger against the field text before the draft is returned, raising `MappingError` on
anything but `validated` / `not_applicable` — not only the cited evidence, because an
uncited span that has drifted is still a broken quote in a stored incident.
`IncidentDraft.unreferenced_evidence_ids` exposes the ledger entries no field points
at — which is deliberately not pruned, since `SpatialHint` and `LanguageInfo` have no
`evidence_ids` slot for the district and language quotes they were built from. It is
the cost of keeping provenance instead of dropping it, and it is measurable: the real
capture's events listing (`NEWS-MDU-0007`, 31 time surfaces in 3519 characters) maps to
37 evidences of which 34 are cited by nothing (§9).

**Determinism.** No clock, no file, no network. `to_storage_document()` for one record
is byte-identical across runs, because Stage 5 sets no `processed_at` — Stage 9 owns
that. Two hashes make a re-run provable: `processing.input_record_hash` over the
canonical `RecordInput` (everything the seam read, including which paths it chose to
ignore) and `processing.config_hash` over the policy fields that change meaning, not
just formatting. The incident id is `INC-<record_id>`: readable, stable for one record,
and explicitly *not* a deduplication device — `positional_record_ids` exists so a feed
whose ids come from scrape order says so in its own warnings.

**What Stage 5 refuses to do.** No deduplication and no clustering: `fingerprint`
stays `None`, `dedup` keeps the contract's `decision=unresolved` default with no
`algorithm_version` and no `cluster_id`, and the `#commentbox` twin pair maps to two
candidates over disjoint evidence ids. No event type, department or
`category_scores`, no relevance, no
summary, no actors, no observations, no location mentions, no coordinates, no gazetteer
lookups, no GIS ids, no OCR, no LLM call, no database, no FastAPI. `inconsistencies()`
on every one of those candidates says so in plain language, and
`derive_review_reasons()` flags `AMBIGUOUS_LOCATION` and `PENDING_GIS_RESOLUTION` for
every Madurai article — a candidate that needs a human is the expected output, not a
failure. Confidence follows the same rule: `overall` is the `min()` of the components
Stage 5 actually established (`language`, `event_time`), `rule` says which components
were combined, and the open sections — the five it never attempts plus severity and
the two spatial gaps — are *counted* in `unresolved_field_count` instead of scored:
8 for every article in the capture. A low `overall` is never manufactured by
averaging in an unasked question.

### Stage 5 behaviours, and where each is pinned

| Required behaviour | Test |
| --- | --- |
| Valid record maps to a candidate | `test_a_valid_record_maps_to_a_candidate_incident`, `test_the_pydantic_record_shape_reads_like_the_dict`, `test_a_record_can_be_read_once_and_mapped_twice` |
| Missing / non-text identity fields rejected by name | `test_a_record_missing_an_identity_field_is_rejected`, `test_a_record_field_holding_a_structure_is_rejected_by_name` |
| Title-only and textless records | `test_a_record_with_a_title_but_no_body_still_maps`, `test_a_record_with_no_readable_text_is_rejected` |
| Dotted paths, unread keys surfaced | `test_dotted_paths_read_nested_values_and_leave_the_rest_as_a_warning`, `test_a_value_outside_the_text_keys_can_never_be_read_as_body_text`, `test_scalar_record_values_are_cited_rather_than_read_as_body_text` |
| Provenance on every evidence item | `test_every_evidence_carries_the_record_it_came_from` |
| Language detected from text, hint inert | `test_tamil_is_decided_from_the_text_not_the_feed_label`, `test_english_is_decided_from_the_text`, `test_the_inherited_language_hint_is_recorded_and_never_load_bearing`, `test_the_language_label_is_cited_as_a_hint_and_never_as_body_text`, `test_a_wrong_language_hint_does_not_override_the_detection`, `test_too_little_text_yields_an_unknown_language_rather_than_a_guess` |
| News `event_time` not trusted | `test_an_articles_own_event_time_is_not_trusted_as_event_time`, `test_a_publish_stamp_stands_in_with_publication_semantics` |
| Relative time via `retrieved_at`, weekday still unplaced | `test_a_relative_tamil_date_is_resolved_against_the_records_own_retrieval_time`, `test_a_bare_weekday_is_never_promoted_to_a_date` |
| Trust only where the policy grants it | `test_a_forecast_valid_date_is_trusted_only_when_the_policy_says_so`, `test_the_same_forecast_is_honest_without_the_trust_policy` |
| District stays a tagged hint | `test_the_feed_district_is_kept_as_an_authority_tagged_hint`, `test_a_record_that_asserts_no_district_asserts_nothing` |
| No place reading, no GIS invented | `test_no_location_mention_is_read_before_stage_six`, `test_no_coordinates_or_gis_identity_are_invented` |
| Severity, status, relevance, classification unresolved | `test_a_source_severity_string_is_cited_without_becoming_a_level`, `test_a_record_with_no_severity_says_so_without_a_citation`, `test_status_is_held_as_provenance_and_never_promoted`, `test_relevance_is_left_unresolved_and_asserts_nothing`, `test_classification_is_left_unresolved_with_no_department_invented` |
| Every span verifies | `test_every_span_bearing_evidence_reproduces_the_field_it_was_cut_from`, `test_verification_replays_the_whole_ledger_not_only_the_cited_evidence` |
| Deterministic mapping | `test_mapping_the_same_record_twice_gives_the_same_incident`, `test_the_input_hash_follows_the_record_and_the_config_hash_follows_the_policy` |
| Real capture, duplicates, id instability | `test_the_capture_maps_to_an_honest_candidate`, `test_the_duplicate_article_pair_stays_two_candidate_records`, `test_the_scrape_position_instability_is_recorded_when_the_policy_declares_it` |
| Time zone declared, ingestion unimported | `test_a_naive_retrieved_at_is_declared_never_assumed`, `test_a_policy_without_a_declared_timezone_says_so`, `test_mapping_never_imports_ingestion` |

`tests/record_fixtures.py` is the other half of that honesty: the Tamil bodies, the
site navigation prefix, the duplicated `UPDATED : … ADDED : …` pair, the `#commentbox`
twin and the `retrieved_at` microseconds are copied out of the October 2026 Madurai
capture, and the shapes that capture does not contain yet — an English article, a
record with an `Orange Alert` severity string, an IMD forecast with a valid
`event_time` — are built against the same field list. `capture()` maps seven of them.

---

## 8. Verification

```
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

Result at this commit: **458 passed** in ~2.3s, 0 failed (112 Stage 1 contracts,
55 Stage 2 spans, 102 Stage 3 language and text representation, 137 Stage 4
temporal — 90 surfaces in `test_time_expressions.py`, 47 roles in
`test_temporal_extraction.py`, 52 Stage 5 record mappings in
`test_record_mapping.py`); `compileall` reports no errors. The Stage 1–3 coverage
tables below are unchanged; Stage 4's behaviour table is in §6 and Stage 5's in §7
because their rules are the subject, not a side effect.

The suite is the specification, and it runs against real Dinamalar Madurai
content rather than synthetic English prose. `tests/builders.py` stores
`CONTENT_TA` (a Tamil road-marker protest report containing the
`ADDED :` / `UPDATED :` publish-stamp boilerplate, a transliterated
`லேண்ட்` and a `4800 மனுக்களை` quantity) and `BENCH_CONTENT_TA` (a Kumbakonam
incident whose text also mentions the Madurai High Court bench). Stage 1 derived
those fixture offsets with `source_text.index(quote)`; Stage 2 reproduces them
through the producer and asserts they are identical, offset for offset.

Stage 5 adds `tests/record_fixtures.py`: the October 2026 Madurai capture held as
Python — the Tamil bodies with their site navigation prefix and duplicated
`UPDATED : … ADDED : …` stamps inside `data.content`, the `#commentbox` twin, the
outage and events listings — plus the shapes the feed could deliver and does not yet
(an English article, a record carrying an `Orange Alert` severity string, an IMD
forecast with a valid `event_time`). Nothing in
`intelligence/tests/` opens `ingestion/data/`, and
`test_mapping_never_imports_ingestion` proves the package boundary twice: a subprocess
imports `intelligence.mapping` and reports nothing ingestion-shaped in `sys.modules`,
and a regex over every file under `intelligence/` fails on a top-level
`import ingestion` / `import app`.

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
Stage 3: `LanguageInfo` accepted a caller-supplied `source` representation
alongside the ones it builds itself, which would have stored the same field text
twice under two ids — `language_info()` now refuses it, because a duplicated
source is how the "original text is canonical" invariant quietly dies.

Stage 4 found seven defects in its own pattern layer while the tests were written,
each one now pinned by the test that fails without the fix: day-part absorption
widened a date and a clock into two overlapping phrases, so
`28 செப்டம்பர் 2026 இரவு 10 மணிக்கு` never became one surface (absorption
now widens in place, and each pass sees the boundaries the previous pass made);
`last night` sat in the day-offset table, which dropped the night and resolved
`exact` (it is a fused form now — `day`, `approximate`); the Tamil weekday rule
also matched English weekday names, so `next Friday` was attributed to a Tamil
rule (the alternation is restricted to Tamil code points); a bare duration
(`இரண்டு நாட்கள்`) was read as a shift with an invented direction (a lead
preposition or an `ago`/`later` marker is now required); `2 ஆம் தேதி` reported
the missing *year* when what was missing was the month (now `NO_MONTH`, a
different question with a different fix); and merging a clock into a date
discarded the day part's meridiem, so `இன்றிரவு 2 மணிக்கு` resolved as 02:00
instead of 14:00 (`DAY_PART_MERIDIEM` carries `am`/`pm` through the merge); and
the colon-clock rule read the `+05:30` tail of an ISO timestamp as a clock
reading, so a clock may no longer start immediately after a sign.

The most consequential Stage 4 finding came from an edge-case probe rather than a
failing test: a publish stamp whose timestamp is ISO or `dd/mm/yyyy` shaped is
*label-isolated* by Stage 3 but leaves its bare timestamp in the body, and because
that timestamp is minute-precise it outranked the article's real event date in
`event_time()`. Stage 3's grammar is not wrong — it declines to parse what it does
not recognise — so Stage 4 now owns the consequence with the stamp-line residue
rule in §6, which holds for stamp shapes neither layer has seen.

Stage 5 changed no earlier-stage code, and its own defect was found the same way: the
first `read_record()` made a text field of *any* path the policy named, so
`extra_keys=("data.station_id",)` put the string `"MDU"` into the record's body and let
three characters join the letter-count vote over a 2377-character Tamil article. The
gate is now `path in {title, *text_keys}`, and
`test_a_value_outside_the_text_keys_can_never_be_read_as_body_text` fails without it.
The other Stage 5 findings are about the data rather than the code, and they are all in
§10: six of the nine articles' own `event_time` disagrees with the stamp printed inside
it, the events listing maps 31 time surfaces into 37 evidences that three fields cite,
and `NEWS-MDU-0005` hands the event slot to a two-year duration.

---

## 9. Known limitations

- **The seam maps one record at a time.** Stage 5 turns one `CommonRecord` into one
  candidate `Incident`; nothing reads a feed, batches records, or decides that two
  records describe one incident. A dotted path is resolved only inside
  `record_input.py`, and only for the paths the seam knows: an unread key *under*
  `data` is reported in `unused_data_keys`, but a top-level field the seam has no slot
  for is dropped with no trace at all. `tests/builders.py` is still hand-written
  evidence from Stages 1-2 — Stage 5's tests never use it, because a real stage picks
  its own quotes.
- **`spans.locate()` is exact-substring only.** Stage 4 is the first consumer of
  the other pattern — it matches over normalised text and cites the original — but
  `locate()` itself is still case- and diacritic-sensitive, and no fuzzy or
  approximate location exists anywhere in the module. A future entity or place
  stage has to decide whether it wants `locate()` or `MappedText.project()`.
- **Verification happens once, at mapping time.** `map_record()` replays every ledger
  span against the field text it read, so no candidate is returned with a broken
  quote. What is still missing is re-verification *after* storage: an `Evidence`
  deliberately does not carry its field's full text, so Stage 9 persistence and
  Stage 8 re-clustering have to re-fetch the source and rebuild the `SourceField`
  before `verify_evidence` means anything about a stored incident.
- **Byte-level source drift is all the hash can prove.** `field_text_hash`
  identifies the text, not the record: if a source edits a headline, every
  Evidence over that field reports `mismatch` together, with no per-quote
  attribution beyond the offsets.
- **Optional is still optional.** A field whose value is unknown can be left
  unset where absence and `UNRESOLVED` mean the same thing. Consumers should
  read the enum members, not rely on `None`, for the four epistemic states.
- **`category_scores` is unconstrained beyond key validity.** Keys must be
  `EventType` values; score ranges and normalisation are a Stage 7 concern.
- **The time-zone policy is a default, not a guarantee.** `TimeValue` requires a
  *declared* timezone or an explicit naive-timestamp inconsistency note; it does not
  decide that India is `Asia/Kolkata`. Stage 5 does declare it —
  `MappingPolicy.timezone` defaults to `Asia/Kolkata`, is hashed into `config_hash()`,
  and a naive `retrieved_at` is warned about instead of converted — but it cannot
  enforce that two records were mapped with the same policy. A caller that passes
  `MappingPolicy(timezone=None)` for one feed produces times that are no longer
  comparable to another feed's, and the only trace of which policy ran is the hash.
- **Gazetteer fields are placeholders.** `LocationMention.gazetteer_matched` /
  `gazetteer_source` record a match the module does not perform.
- **Vocabulary is a first draft.** The 70 event types and 33 departments cover
  the categories visible in current samples plus standard district
  administration; they will need review with the collector's office.
  `TAXONOMY_VERSION` exists so a change is visible in stored incidents.
- **Windows console encoding.** Tamil output crashes a cp1252 stdout; run
  diagnostics with `PYTHONIOENCODING=utf-8`. Later stages that print extracted
  text must not assume a UTF-8 console.
- **Language detection is a script ratio, not a model.** The 0.8 / 0.1
  thresholds are chosen for Tamil newsprint and are named constants, not derived
  from a corpus. Consequences: Latin-script text with no English function word is
  reported `und` (French or German copy is *not* silently filed as English);
  digits, punctuation and marks are excluded, so a numeral-heavy headline does
  not dilute a Tamil ratio - and a field with no letters at all is `und`;
  three-script articles are only ever `und` or `mul`, because `other` has no
  language to name. `LanguageDetection.script_ratios` exposes the evidence so a
  reviewer can see why.
- **Detection is per-field, and records are aggregated by letter count.**
  `_aggregate()` lets the field with the most letters decide a record, so a long
  English caption cannot outvote a Tamil body - but a record whose title and body
  disagree is represented by one map entry per record plus the aggregate.
- **Morphology is a candidate generator with three sandhi rules.** Modelled: a
  dropped final `ம்`, a dropped `்` before a vowel-sign suffix, and the illegal
  `ை`+vowel junction. Not modelled: `கு`→`க்` u-deletion (`வழக்கில்` is a lookup
  miss), plural-then-case compounds read one suffix at a time, the `க்`
  augmentation in `மதுரைக் கிளை`, verb inflection entirely, and any ambiguity
  resolution between candidate stems. Nothing asserts a stem; a downstream stage
  must treat the list as possibilities.
- **Boilerplate covers three labels and two month vocabularies.** `ADDED`,
  `UPDATED`, `PUBLISHED` with Tamil or Latin month abbreviations, `month d, yyyy`
  dates and `h:mm AM/PM` times (a stamp wrapped over a newline still matches).
  Indian `dd/mm/yyyy`, ISO `2026-10-02T09:46` and bare weekday names are not
  recognised as timestamps; the label is still isolated, and Stage 4's
  stamp-line residue rule keeps the unparsed timestamp out of the event slot — so
  the miss costs a `publication_time`, never a wrong `event_time`. A label that
  is not one of the three words (`PUBLISHED ON`, `வெளியானது`) is not a stamp at
  all, and its timestamp is then read as ordinary body text.
- **The event slot is a ranking rule, not a reading of the article.** A field
  with several dates gives `event_time()` to the finest, then the unambiguous,
  then the earliest — `செப் 28 … செப் 30 …` picks the 28th because it is printed
  first, not because the writer meant it. Nothing binds a date to the clause it
  is about; that needs subject/verb attachment, which is a later stage's problem,
  and the runner-up mentions stay visible in `TemporalExtraction.mentions`.
  Stage 5 inherits the worst case, and it is not a ranking one: in the real capture,
  `NEWS-MDU-0005`'s only time surface is `இரண்டு ஆண்டுகளாக` ("for two years"),
  which Stage 4 resolves to `2024-01-01T00:00` at **year** precision with
  `semantics=event_time`, `method=rule`, `confidence=0.8`. The mention honestly records
  `is_interval=True`, but `event_time()` still hands the start instant to the slot, so
  the incident reads as if something happened at a moment when the source described a
  two-year state. `_event_time()` trusts Stage 4's answer, which is the right division
  of labour — the fix belongs to `event_time()`, not to the seam.
- **`reference` is taken on trust.** Every relative surface is arithmetic against
  the datetime the caller passes, and the module never checks that the reference
  agrees with the absolute dates printed in the same field. A wrong reference
  silently shifts `நேற்று`. Stage 5 removes one guesser and adds another: the
  reference is now `record.retrieved_at`, which is this module's own clock at
  scrape time, so a mis-set scraper date shifts every relative expression in a
  feed at once. The guard is still unwritten — a check that the reference is not
  before, or absurdly after, the stamps printed in the article.
- **A reporting cue is 17 phrases inside a 90-character sentence.** No verb
  morphology, no dependency parse, no hearsay grading. An unlisted paraphrase
  leaves the mention `event_time`: `தெரிவித்தனர்` (they informed) is a cue,
  `தெரிவித்தார்` (he/she informed) is not, so the same sentence changes role with
  its subject. That is the safe direction only because the alternative is
  demoting real event times.
- **Two calendars and one era are missing.** Tamil month names of the traditional
  calendar (`ஆவணி`, `புரட்டாசி`, `மார்கழி`) produce no surface at all, the
  Christian-era assumption is silent (`5482 ஆம் ஆண்டு` is out of
  `MIN_YEAR..MAX_YEAR` and simply vanishes), and two-digit years (`28.09.26`)
  are refused rather than guessed. Each is a vocabulary or rule addition, not a
  contract change.
- **Weekdays and bare clocks are never placed.** `திங்கட்கிழமை` is always
  unresolved (`NO_ANCHOR`) and `காலை 10:30 மணிக்கு` always `NO_DATE`, because
  picking "the next Monday" or "the day of publication" would be an invented
  fact. They are still emitted as mentions with their spans so a reviewer sees
  the shape that was not resolvable. The fused adverbial is only half covered:
  `வெள்ளியன்று` and `சனியன்று` read as weekdays, but `செவ்வாயன்று`,
  `புதனன்று` and `வியாழன்று` do not, because their stems lose a `்` before the
  suffix and `WEEKDAYS` stores the pulli spelling — the same elision Stage 3
  morphology lists as unmodelled.
- **No time arithmetic across fields.** Stage 4 reads one field at a time, so a
  title's date cannot corroborate or contradict the body's. Stage 5 could now run both
  — it holds both `SourceField`s — and deliberately does not: `Incident.reported_at`
  stays unset and the body's answer wins `event_time` outright. Comparing two fields
  needs a rule for what a disagreement *means*, which is a review signal no
  `ReviewReason` expresses yet.
- **The transliteration table is a press convention, not a standard.** `த`→`th`
  and `ட`→`t` distinguish the two series, `ண`/`ன`/`ந` all collapse toward `n`,
  and inherent vowels appear unless a pulli kills them - so `kamishannar` is a
  faithful rendering, not the spelling a district report would print. That is why
  the vocabulary is checked first and the table rendering stays labelled
  `basis="table"`. `மாநகராட்சி`→"Managaram" is carried over from the Stage 1
  fixture and needs reviewer confirmation before it is trusted for matching.
- **The position map is not persisted.** `TextRepresentation` stores
  `offsets_align_with_source`, not `MappedText.positions`. A stored Incident can
  prove *whether* a representation was aligned but cannot re-project a derived
  offset without re-running the derivation over the same source text - which is
  exact, deterministic, and needs the original field.
- **Ten vocabulary entries and no word-frequency evidence.** The transliteration
  vocabulary is the loanwords and place names visible in the current fixtures; a
  new district term is a code change, not a learning step.
- **A candidate carries its whole ledger, cited or not.** `Incident.evidence` holds
  every span Stage 5 produced, and most of it is cited by nothing: the real capture's
  events listing maps to 37 evidences with 34 unreferenced. The contract has no
  `evidence_ids` slot on `SpatialHint.district_hint` or
  `LanguageInfo.inherited_language_hint`, so their quotes have nowhere to be *pointed
  at* from — dropping them would lose provenance, keeping them makes the ledger the
  size of the extraction. `unreferenced_evidence_ids` exists so Stage 9 can decide
  which of the two it wants to pay for.
- **`record_type` is trusted, not checked.** `time_trusted_record_types` is matched
  against whatever string the producer wrote in `record_type`, so a news article
  mislabelled `"forecast"` gets its page stamp promoted to event time with
  `confidence=1.0`. This is the only place in the seam where a source can talk itself
  into authority, and the mitigation is policy discipline: the set is per-feed, it is
  hashed into `config_hash()`, and the record's own claim is cited as a span, so a bad
  trust decision is at least reconstructible.
- **The policy is the mapping's blind spot.** `text_keys` and `extra_keys` must be
  declared for each feed shape; a `data` key nobody listed is reported in
  `unused_data_keys` and otherwise invisible, and a *new top-level* field the seam has
  no slot for is dropped with no trace (§9, first bullet). Nothing in `intelligence/`
  can tell that a policy stopped matching a producer's output until someone reads the
  warnings.
- **`pipeline_version` is not this module's version.** `MappingPolicy` defaults to
  `"0.1.0"`, which is `ProcessingMetadata`'s own contract default rather than
  `intelligence.__version__`, because the deployment's release number is a fact the
  seam cannot know. What a stored incident *does* record is `stage_versions`
  (`schema 1.0, evidence 2, language 3, temporal 4, mapping 5`) and `config_hash`, so a
  re-run under different rules is detectable even when nobody bumped a version string.
- **Every real article is flagged for review.** With the default
  `low_confidence_threshold=0.6`, each capture record collects six or seven reasons —
  `unresolved_event_type`, `unresolved_relevance`, `unresolved_severity`,
  `no_event_location_candidate`, `pending_gis_resolution`, `ambiguous_location`, and
  usually `publication_time_only`. That is accurate, not broken: a review queue fed by
  Stage 5 alone contains every article. Stage 7's relevance decision is what makes the
  queue mean anything.
- **A title-only record cannot name its language.** When a policy reads no body field,
  `primary_language` stays `und` and detection is a one-line script ratio over the
  headline — the honest outcome, but it means `data.forecast` must be in `text_keys`
  for a weather record to be identified as the language it is written in.

## 10. External integration dependencies

Reported, not fixed. The repository outside `intelligence/` was not modified.

1. `CommonRecord.event_time` carries the wrong kind of time for a news article.
   `ingestion/app/connectors/news/dinamalar.py:196-206` finds the page's `ADDED : …`
   line, `parse_dinamalar_date()` (`:72-95`) turns it into a real `datetime`, and
   `ingestion/app/normalizers/news.py:24` assigns it to `event_time`. That is the
   *first* stamp, while the page prints `UPDATED : …` above it and the two differ: six
   of the nine records on the October capture disagree with themselves, e.g.
   `NEWS-MDU-0001` has `event_time=2026-10-02T17:45` but an in-content
   `UPDATED : அக் 03, 2026 12:00 AM`. The three that agree print only an `ADDED`
   line.
   `event_time` is what Intelligence is supposed to *derive*, not receive, and Stage 5
   now treats it accordingly — the value is preserved and warned about, never used as
   event time for `record_type="article"` (§7). Ingestion either passing `None` or
   renaming the field to `published_at` would let the seam stop explaining itself
   (owner: ingestion).
2. `record_id` is `f"NEWS-MDU-{index:04d}"` (`news.py:14`), derived from the scrape's
   enumeration index, so it changes whenever feed order or page size changes.
   `Evidence.record_id` and `Incident.supporting_record_ids` assume stable ids, so dedup
   and cross-source linking are not trustworthy until the id is content-derived
   (owner: ingestion). Stage 5 makes the consequence visible rather than theoretical:
   `incident_id` is `INC-<record_id>`, so re-scraping the same article in a different
   position yields a *new incident id for the same article*, and
   `MappingPolicy(positional_record_ids=True)` records that in the incident's own
   warnings. The policy flag only declares the instability; only ingestion can fix it.
3. `Location(raw_text="Madurai", district="Madurai", state="Tamil Nadu")` is
   hardcoded for every article (`news.py:26-29`, and again in
   `normalizers/weather.py:31-34`). Contract-wise this is handled
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
7. `ingestion/app/normalizers/news.py:34` hardcodes `data["language"] = "ta"` for
   every article, so the field cannot be used to evaluate detection: a Tamil
   verdict on a Tamil feed is indistinguishable from that constant being copied
   through. Stage 3 records it as `inherited_language_hint` only
   (`hint_conflict()` reports a disagreement), and any accuracy claim for
   language detection needs a labelled corpus the ingestion layer does not
   produce. Stage 5 added the other half of the protection: the value can no longer
   even be read as body text, and a hint that contradicts the script evidence becomes
   a warning on the incident instead of a silent override (§7).
8. The publish stamp reaches Intelligence three times, not twice: inside
   `data["content"]` (`news.py:33` passes `article.content` verbatim, including the
   *duplicated* `UPDATED : … ADDED : …` pair), as the parsed `event_time`
   (`news.py:24`), and as nothing else. Stage 3 isolates it, Stage 4 files the
   in-content one as `publication_time`, and Stage 5 compares the two and warns; on the
   real capture they disagree on six of nine records because of item 1, and the
   duplicated pair means `publication_time()` picks the *later* `UPDATED` stamp. If
   ingestion ever deduplicates the stamps or drops the raw string, that agreement stops
   being checkable (owner: ingestion).
9. `retrieved_at` has two different shapes in one pipeline: `news.py:39` writes
   `datetime.now().isoformat()` (naive, host-local) and `weather.py:53-55` writes
   `datetime.now(timezone.utc).isoformat()` (offset-aware). Stage 5 uses `retrieved_at`
   as the reference clock for relative expressions, so news times are pinned to an
   undeclared local zone while weather times are not, and the two are not comparable
   until someone declares which one is authoritative. `MappingPolicy` cannot fix it:
   the seam reports the naive value and refuses to convert it, which is the only
   defensible behaviour when the offset is genuinely unknown (owner: ingestion).
10. `weather.py:13-14` builds the forecast date as
    `f"{weather.forecast_date}-{datetime.now().year}"` from IMD's `"02-Oct"`, so a
    forecast captured in December is silently dated the *following* year, and
    `record_id=f"WEATHER-MDU-{weather.forecast_date}"` (`:19`) keys identity on that raw
    string, so next year's 2 October collides with this year's. This is the one record
    type whose time Stage 5 *is* allowed to trust (`time_trusted_record_types`), because
    a forecast states its own valid date rather than copying a page stamp — which makes
    the year inference the most expensive place for it to be wrong. No weather record is
    on disk in this workspace to check it against, so this is read from the code and the
    Stage 5 forecast fixture, not observed (owner: ingestion).
11. `severity` and `status` are never set by `news.py`, and all nine records in
    `ingestion/data/normalized/dinamalar/20261003_085226.json` have both `null`.
    `weather.py:41` passes IMD's `warning` string through inside `data`, which is not a
    `CommonRecord.severity` at all. So the cited-severity path Stage 5 built
    (`_severity()`, §7) is exercised only against a synthetic orange-alert fixture and no
    real record yet tests it. Ingestion needs a severity vocabulary before
    `SeverityLevel` mapping is anything but a guess (owner: ingestion).

## 11. Next: Stage 6 — place and actor mentions

Stage 5 hands the pipeline a real `Incident` per record, with two holes shaped
exactly like the contract: `spatial.mentions` is empty and `actors` is empty. Stage 6
fills them the way Stage 4 filled time — deterministic surfaces in one module, roles
in another, and no coordinate, department or name that the text did not print.

1. `intelligence/extraction/place_expressions.py` — location surfaces over the
   same stamp-free body: Tamil place words carrying a case suffix (`மதுரையில்`,
   `கும்பகோணத்திலிருந்து`), `… மாவட்டம்` / `… district`, habituation words
   (`தென்`, `வட`, `அருகே`), and route or number-plate markers. Reuse
   `morphology.suffix_candidates()` as the candidate generator it is: a place
   phrase is matched through derived text and cited with `MappedText.project()` +
   `evidence_at()`, never by re-typing an offset.
2. `intelligence/extraction/places.py` — the role layer, mirroring `temporal.py`:
   `LocationMention`s with `role` set by a stated rule, `district_hint_from_text`
   filled only from a phrase that says *district* — which is what lets
   `derive_review_reasons()` stop reporting `AMBIGUOUS_LOCATION` for an article that
   names its own district — and `GisResolution` left `pending_gis` because geocoding
   is not this module's to invent. `best_event_location_mention_id` is set only when
   one mention is unambiguously the event place; "Madurai High Court bench" stays a
   mention, not a location.
3. `intelligence/extraction/actors.py` — `Actor` surfaces: official titles in both
   scripts (`கமிஷனர்`, `Collector`, `மேயர்`), department names reachable through
   `config.vocabularies`, and the Tamil postpositions morphology already models
   (`-இடம்`, `-சார்`, `-விடம்`). An actor with no cited span is not an actor.
4. `mapping/assembly.py` then has something to put the mentions *into*: `_spatial()`
   stops being a hint-only function, `report()` gains a place and actor block, and the
   `unresolved_field_count` drops by whatever Stage 6 genuinely established — never by
   more. The evidence ledger absorbs the new spans, so §7's whole-ledger verification
   is the safety net rather than new machinery.

Already decided, so Stage 6 does not have to decide again: the record seam exists
(§7), `retrieved_at` is the reference clock and `Asia/Kolkata` the declared zone, both
on `MappingPolicy` — the item this plan used to carry forward as "the reference clock
finally gets an owner". `திங்கட்கிழமை` and a bare `காலை 10:30 மணிக்கு` stay
unresolved anyway, because a weekday and an hour with no date are not facts this
module can invent; now they at least have a stated clock to be unresolved against.

Carried forward, because each one is a decision and not a pattern:

- **Relevance and classification stay Stage 7.** `event_type`, `departments`,
  `category_scores` and `relevance` are unresolved in every Stage 5 candidate, and
  `INFORMATIONAL_EVENT_TYPES` is the prior they will be decided against.
- **Deduplication stays Stage 8.** The `#commentbox` twin pair maps to two candidates
  today; the fingerprint that joins them is a decision with a threshold and a
  reviewer, not a hash Stage 5 may quietly take.
- **Stamp label vocabulary.** `PUBLISHED ON` and a Tamil `வெளியானது` are not
  labels, so their timestamps are read as body text; the residue rule catches the
  line-local case, not the unlabelled one.
- **Traditional Tamil month names** (`ஆவணி`, `புரட்டாசி`, `மார்கழி`) produce no
  surface at all — a vocabulary addition, but only worth doing if the collector's
  office actually receives such reports.
- **Cross-field time reconciliation.** A title date and a body date that disagree is a
  review signal, and `derive_review_reasons()` has no reason for it yet. Stage 5 makes
  this reachable rather than hypothetical: it is the first stage that holds both
  fields' extractions at once.
- **An interval can win the event slot.** `event_time()` hands `இரண்டு ஆண்டுகளாக`
  to `Incident.event_time` as a start instant (§9), which reports a two-year state as a
  moment. The fix is in the accessor — exclude `is_interval` surfaces or carry their
  endpoints — not in Stage 6's patterns, and Stage 5 leaves the seam's trust rule alone
  so there is exactly one place where the decision gets made.
- **A place-name gazetteer** is the first thing Stage 6 will be tempted to add. It is a
  dependency decision for the team, not a file to drop into `config/`.

Stage 6 adds no dependency: place and actor surfaces are `re`, the dictionaries already
in `config/` and `extraction/morphology.py`, and the same `MappedText` projection
Stage 3 built — plus the ledger Stage 5 already verifies, which is where a hand-typed
offset would have to go to be a problem.
