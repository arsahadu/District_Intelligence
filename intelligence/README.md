# Intelligence Module

AI / NLP intelligence layer for the Collector's District Intelligence Platform.

Owner: Member 1 (AI / Intelligence). This file is the durable technical record of
the module. It documents only what is implemented **and tested** today, plus the
contract guarantees later stages and other modules rely on.

Current state: **Stages 1-4 complete — contracts, the evidence span primitive,
the language / text representation layer, and the temporal extraction layer.**
There is still no CommonRecord → Incident pipeline: nothing here reads a network,
a database, or a file, and no text is classified as an event type, located or
scored for severity yet. What Stage 4 adds is the answer to *when*, with the same
evidence discipline as *where it is written*.

```
CommonRecord ──> [ Intelligence ] ──> Incident ──> Evidence / Provenance
                     │                    │
              stages 1-4               dedup ──> trends / priority / summaries
        contracts, spans, text,                  │
        time and roles                   Collector Copilot
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

| Package | Purpose |
| --- | --- |
| `intelligence/models/` | Structure: field shapes, types, invariants. |
| `intelligence/config/` | Taxonomy data: event families, department mapping, ordering. |
| `intelligence/extraction/` | Evidence production, text representation and time: spans, hashes, verification, language, normalisation, morphology, stamps, transliteration, temporal surfaces, temporal roles. |
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
    test_language_detection.py              21 tests  (Stage 3)
    test_normalization.py                   21 tests  (Stage 3)
    test_morphology_boilerplate.py          34 tests  (Stage 3)
    test_transliteration.py                 26 tests  (Stage 3)
    test_time_expressions.py                90 tests  (Stage 4)
    test_temporal_extraction.py             47 tests  (Stage 4)
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
`time_value()` — the Stage 1 `TimeValue`, which is what `Incident.event_time`
will hold in Stage 8. `as_dict()` on the extraction serialises the roles and the
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

## 7. Verification

```
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

Result at this commit: **406 passed** in ~0.7s, 0 failed (112 Stage 1 contracts,
55 Stage 2 spans, 102 Stage 3 language and text representation, 137 Stage 4
temporal — 90 surfaces in `test_time_expressions.py`, 47 roles in
`test_temporal_extraction.py`); `compileall` reports no errors. The Stage 1–3
coverage tables below are unchanged; Stage 4's behaviour table is in §6 because
its rules are the subject, not a side effect.

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

---

## 8. Known limitations

- **No pipeline.** Nothing converts a `CommonRecord` into an `Incident` yet.
  Stages 3 and 4 read and re-represent text a caller hands them, but no stage
  decides what an article is about, and `tests/builders.py` is still hand-written
  evidence: a real stage picks the quotes. Resolving a dotted `field` path
  against a record object is deliberately not implemented — that is the
  CommonRecord → Intelligence seam, still a later stage.
- **`spans.locate()` is exact-substring only.** Stage 4 is the first consumer of
  the other pattern — it matches over normalised text and cites the original — but
  `locate()` itself is still case- and diacritic-sensitive, and no fuzzy or
  approximate location exists anywhere in the module. A future entity or place
  stage has to decide whether it wants `locate()` or `MappedText.project()`.
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
  that India is `Asia/Kolkata`. Stage 4 carries a declaration through — the
  caller's `timezone`, or a zone the text names — and will not invent one, so the
  policy is still the pipeline's: every record from an Indian feed has to be
  extracted with the same argument or the stored times stop being comparable.
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
- **`reference` is taken on trust.** Every relative surface is arithmetic against
  the datetime the caller passes, and the module never checks that the reference
  agrees with the absolute dates printed in the same field. A wrong reference
  silently shifts `நேற்று`; the natural guard is a Stage 8 sanity check that
  `reference` is not in the future relative to the article's own stamps.
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
  title's date cannot corroborate or contradict the body's, and
  `Incident.reported_at` cannot be assembled from a record until the mapping
  stage decides which field wins.
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

## 9. External integration dependencies

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
7. `ingestion/app/normalizers/news.py:34` hardcodes `data["language"] = "ta"` for
   every article, so the field cannot be used to evaluate detection: a Tamil
   verdict on a Tamil feed is indistinguishable from that constant being copied
   through. Stage 3 records it as `inherited_language_hint` only
   (`hint_conflict()` reports a disagreement), and any accuracy claim for
   language detection needs a labelled corpus the ingestion layer does not
   produce (owner: ingestion).
8. The publish stamp is inside `data["content"]` (`news.py:33` passes
   `article.content` verbatim) *and* is what `published_at` was built from, so the
   same timestamp reaches Intelligence twice with different shapes. Stage 3 keeps
   both visible: `boilerplate.split()` isolates the stamp from the body and
   `timestamp_evidence()` cites it as printed. Stage 4 now reads the in-content
   one and files it as `publication_time`, so the two only meet at the mapping
   stage — which needs ingestion to stop dropping the raw string in order to
   compare them and report a disagreement (owner: ingestion).

## 10. Next: Stage 5 — place and actor mentions

Stage 4 hands the pipeline one honest answer per field: `event_time`,
`reported_time`, `publication_time` and `retrieval_time`, each either a cited
`TimeValue` or an explicit absence, plus the mentions it could not resolve and the
reason for each. Stage 5 does for *where* and *who* what Stage 4 did for *when*:
deterministic surfaces in one module, decisions in another, and no coordinate,
department or name that the text did not print.

1. `intelligence/extraction/place_expressions.py` — location surfaces over the
   same stamp-free body: Tamil place words carrying a case suffix (`மதுரையில்`,
   `கும்பகோணத்திலிருந்து`), `… மாவட்டம்` / `… district`, habituation words
   (`தென்`, `வட`, `அருகே`), and route or number-plate markers. Reuse
   `morphology.suffix_candidates()` as the candidate generator it is: a place
   phrase is matched through derived text and cited with `MappedText.project()` +
   `evidence_at()`, never by re-typing an offset.
2. `intelligence/extraction/places.py` — the role layer, mirroring `temporal.py`:
   `LocationMention`s with `role` set by a stated rule, `SpatialHint.district_hint`
   filled only from a phrase that says *district*, and `GisResolution` left
   `pending_gis` because geocoding is not this module's to invent.
   `best_event_location_mention_id` is set only when one mention is unambiguously
   the event place; "Madurai High Court bench" stays a mention, not a location.
3. `intelligence/extraction/actors.py` — `Actor` surfaces: official titles in both
   scripts (`கமிஷனர்`, `Collector`, `மேயர்`), department names reachable through
   `config.vocabularies`, and the Tamil postpositions morphology already models
   (`-இடம்`, `-சார்`, `-விடம்`). An actor with no cited span is not an actor.
4. The reference clock finally gets an owner. `திங்கட்கிழமை` and a bare
   `காலை 10:30 மணிக்கு` stay unresolved until something supplies `reference`, and
   the only defensible pair is `retrieved_at` plus `publication_time()` — that is a
   Stage 8 mapping decision, and Stage 5 should leave the same `None` behaviour
   intact rather than paper over it.
5. Still no event type, severity, dedup or incident assembly. Relevance and
   classification stay Stage 6, `CommonRecord` → `Incident` stays Stage 8, and
   `category_scores` stays empty until the taxonomy is reviewed with the
   collector's office.

Carried forward, because each one is a decision and not a pattern:

- **Reference policy** (item 4) and the time-zone declaration — both belong to the
  mapping stage, and both need to be constant across a feed for stored times to be
  comparable.
- **Stamp label vocabulary.** `PUBLISHED ON` and a Tamil `வெளியானது` are not
  labels, so their timestamps are read as body text; the residue rule catches the
  line-local case, not the unlabelled one.
- **Traditional Tamil month names** (`ஆவணி`, `புரட்டாசி`, `மார்கழி`) produce no
  surface at all — a vocabulary addition, but only worth doing if the collector's
  office actually receives such reports.
- **Cross-field time reconciliation.** A title date and a body date that disagree
  is a review signal, and `derive_review_reasons()` has no reason for it yet.
- **A place-name gazetteer** is the first thing Stage 5 will be tempted to add.
  It is a dependency decision for the team, not a file to drop into `config/`.

Stage 5 adds no dependency: place and actor surfaces are `re`, the dictionaries
already in `config/` and `extraction/morphology.py`, and the same `MappedText`
projection Stage 3 built.
