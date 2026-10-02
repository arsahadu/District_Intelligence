# Intelligence Module

AI / NLP intelligence layer for the Collector's District Intelligence Platform.

Owner: Member 1 (AI / Intelligence). This file is the durable technical record of
the module. It documents only what is implemented **and tested** today, plus the
contract guarantees later stages and other modules rely on.

Current state: **Stages 1-3 complete — contracts, the evidence span primitive,
and the language / text representation layer.** There is still no CommonRecord →
Incident pipeline: nothing here reads a network, a database, or a file, and no
text is classified as an event type, timed, located or scored for severity yet.

```
CommonRecord ──> [ Intelligence ] ──> Incident ──> Evidence / Provenance
                     │                    │
              stages 1-3               dedup ──> trends / priority / summaries
        contracts, spans, text                   │
        representations                 Collector Copilot
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

| Package | Purpose |
| --- | --- |
| `intelligence/models/` | Structure: field shapes, types, invariants. |
| `intelligence/config/` | Taxonomy data: event families, department mapping, ordering. |
| `intelligence/extraction/` | Evidence production and text representation: spans, hashes, verification, language, normalisation, morphology, stamps, transliteration. |
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
    language.py                script profiles, LanguageAssessment, detection,
                               LanguageInfo assembly, hint auditing
    normalize.py               MappedText, cluster_end, position-preserving normalize
    morphology.py              SUFFIX_RULES, tokenize, suffix/surface candidates
    boilerplate.py             ADDED/UPDATED stamps, BodySplit, body representation
    transliteration.py         script table, curated vocabulary, Latin candidates
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

## 6. Verification

```
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

Result at this commit: **269 passed** in ~0.6s, 0 failed (112 Stage 1 contracts,
55 Stage 2 spans, 102 Stage 3 language and text representation); `compileall`
reports no errors.

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

---

## 7. Known limitations

- **No pipeline.** Nothing converts a `CommonRecord` into an `Incident` yet.
  Stage 3 reads and re-represents text a caller hands it, but no stage decides
  what an article is about, and `tests/builders.py` is still hand-written
  evidence: a real stage picks the quotes. Resolving a dotted `field` path
  against a record object is deliberately not implemented — that is the
  CommonRecord → Intelligence seam, still a later stage.
- **Spans are exact-substring only.** No case-insensitive, diacritic-insensitive
  or fuzzy location: `normalize()` now exists to build the representation such a
  match runs against, and the hit is projected back onto the original — but no
  stage calls it that way yet.
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
  recognised as timestamps; the label is still isolated, so the body stays clean
  and Stage 4 sees the miss as a `None` timestamp rather than a wrong time.
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

## 8. External integration dependencies

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
   `timestamp_evidence()` cites it as printed. Stage 4 needs ingestion to stop
   dropping the raw string before it can compare the two (owner: ingestion).

## 9. Next: Stage 4 — event time

Stage 3 leaves Stage 4 two clean inputs per record: a stamp-free body
representation whose offsets still point at the original field, and every
printed stamp pinned as `publication_time` Evidence with the note "not event
time".

1. `intelligence/extraction/time_expressions.py` — deterministic surface
   patterns: Tamil and Latin month forms, day + year, clock times, and deictic
   words (ின்ரு, நேர்று, கலை, பிறன், இன்னெ) carrying an
   explicit `TimeQualifier`. Every hit is cut with `locate()` over the original
   field, so a `TimeValue.raw_text` is always citable.
2. Semantics is the decision, not parsing. A `boilerplate` stamp can only become
   `TimeValue(semantics=publication_time)`; an expression inside the body may be
   `event_time`, `reporting_time` or `unresolved`, chosen by a stated rule and
   recorded with `method` and `evidence_ids`. A timestamp is never promoted to
   event time because nothing better was found — that is what
   `derive_review_reasons()` reports today.
3. Matching runs on derived text, citation on original text: month and weekday
   lookups use `normalize_field()` and project the hit back, so "அக்." and
   "அக்டோபர்" share one code path without bending offsets.
4. Time zone is configuration, not inference. `TimeValue.timezone` is declared
   (`Asia/Kolkata`) where the pipeline assumes it; a naive stamp keeps its
   explicit naive-timestamp inconsistency note rather than silently gaining an
   offset.
5. Precision comes from what the surface says. `TimeValue.precision` is `minute`,
   `hour`, `day`, `month` or `year` according to the matched text; a vague "last
   week" stays `approximate` or `unresolved` instead of an invented midnight.
6. Still no incident assembly. Stage 4 emits `TimeValue`s and their Evidence;
   relevance, classification, location, dedup and the CommonRecord → Incident
   mapping remain Stages 5–9.

Stage 4 adds no dependency: date surfaces are matched with `re`, the Unicode
tables already in `boilerplate.py`, and `datetime` from the standard library.
