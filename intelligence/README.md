# District Intelligence — Intelligence Module

## 1. Boundary

This package is the AI/NLP intelligence layer of the Collector's District Intelligence Platform. It reads
source-independent `CommonRecord` objects from Data Integration and returns structured, evidence-grounded `Incident`s.

**Intelligence lives entirely inside `intelligence/`.** It imports nothing from `ingestion/`, `backend/`, `frontend/` or
GIS — it reads records by declared field path against a structural protocol, so a renamed or missing field fails loudly
instead of surfacing as a silent `None`. Where it does talk to the platform it talks HTTP: the orchestrator `GET`s
CommonRecords from the API and posts nothing back, and it never opens a database connection. Nothing outside this
directory is changed to make it work, and no other team is required to change its contract to consume it.

| Owner | Responsibility |
| --- | --- |
| Ahad — Data Integration | Source → `CommonRecord` |
| Prem — Intelligence | `CommonRecord` → validated `Incident` (this module) |
| Alagan — GIS | Place mentions → canonical geography, coordinates, visualisation |
| Alex — Platform | `Incident` → PostgreSQL, FastAPI, Collector workspace |

## 2. The chain

```
Source (Dinamalar news, IMD weather, AgMarkNet markets, departmental feeds)
  ↓
Connector + normaliser                          — Data Integration (not this module)
  ↓
Platform API: GET /records                      — Alex's FastAPI over http(s), one CommonRecord per object
  ↓
pipeline.run_pipeline                           — read the batch, one record at a time, collect and report
  ↓
pipeline.process_record (per record)            — config → provider → extraction → validated incident
  ↓
context.build_context                           — read fields verbatim, one SourceField per citable field
  ↓
intelligence.extraction_request                 — one prompt: the record's fields + the taxonomy tokens
  ↓
LLMProvider.generate_structured                 — llm.py: one model, via the groq SDK or any OpenAI-compatible endpoint
  ↓
ExtractionDraft                                 — wire model: all-optional, extra keys ignored
  ↓
intelligence.validate_extraction → _assemble    — span replay, enum coercion, id assignment
  ↓
contract.Incident (schema 2.0)                  — claims + evidence + validation + generation
  ↓
Platform / GIS / Collector workspace
```

`python -m intelligence.pipeline` is the one runnable entry point; `pipeline.process_record(record)` is the one-record
boundary it drives. `context.build_context`, `intelligence.extraction_request`, `intelligence.extract_incident` and
`intelligence.validate_extraction` are callable directly for stage-by-stage testing.

**One chain, three sources.** `data` is source-specific while the CommonRecord around it is not, so the context builder
discovers the keys a record actually carries instead of assuming a news-shaped one: a Dinamalar article offers
`data.content`, an IMD record `data.warning`/`data.forecast` and its temperatures, a market line `data.commodity`,
`data.market` and its prices. Strings and rendered numbers both become citable fields, so a quote of `50.0` from
`data.max_price` grounds exactly like a Tamil sentence. There is no `NewsPipeline`/`IMDPipeline`/`AgriPipeline` and no
field list per `source_type` — `source_type` and `record_type` reach the model as context, and the prompt tells it that
a field another source usually carries does not exist here unless it is printed.

## 3. Modules

| File | Responsibility |
| --- | --- |
| `contract.py` | The stable output contract: `Incident`, `Claim`, `Location`, `Entity`, `Relationship`, `Provenance`, `Validation`, `Generation`, `Issue` and the enums they use |
| `context.py` | `RecordContext` — every field the record actually carries as a citable `SourceField`, plus their hash, prompt block and provenance |
| `llm.py` | Provider abstraction: `LLMConfig`, `LLMRequest`, `LLMResponse`, `LLMProvider`, `GroqProvider`, `OpenAICompatibleProvider`, `build_provider` |
| `intelligence.py` | Orchestration: request building, draft parsing, grounding, assembly into an `Incident` |
| `pipeline.py` | The runnable orchestrator: `process_record` for one record, `fetch_records`/`fetch_record` over the API, `run_pipeline` for a batch, `print_report` and `main` for the console |
| `models/`, `extraction/`, `mapping/`, `config/` | Deterministic foundations: five modules shared with the LLM path, the rest frozen (§9) |

## 4. The Incident contract

`contract.Incident` is what the platform persists. `schema_version = "2.0"`.

| Section | Holds |
| --- | --- |
| `incident_id`, `provenance` | `INC-<record_id>` and the whole source trail: record id, source id/type, record type, `source_url`, `raw_reference`, `retrieved_at`, modality, language/district/state hints, declared severity and status, `input_hash` |
| `title`, `description` | what the record printed, grounded by echoing it |
| `incident_type`, `category`, `department`, `severity`, `priority`, `event_status` | controlled taxonomy tokens, or `unresolved` / `unknown` |
| `event_time`, `event_time_precision` | the instant the source stated, with its precision |
| `locations`, `entities`, `relationships` | quoted mentions with role and granularity; typed parties; links by assigned id |
| `evidence`, `claims` | every span pinned to a field with offsets and a validation state; one claim per field with method, confidence and the evidence it cites |
| `validation` | `accepted` / `review_required` / `unresolved`, plus every `Issue` found |
| `generation` | provider, model, prompt version, input hash, latency, warnings |

The taxonomy enums exist so results can be stored and filtered. They are the only controlled vocabulary in the new
path — classification is the model's job, not a phrase list's.

`unresolved` and `unknown` are legal values, not failures. A gap is stated, never papered over. `priority` is nullable,
because a system that has not weighed the district yet should not pretend to have ranked it. The LLM does not get a
canonical id or coordinate field to answer into at all, so inventing one is structurally impossible.

## 5. Review states

- **`accepted`** — the claim or item is grounded and no finding touched it.
- **`review_required`** — something is grounded, but a value was refused, a quote was ambiguous, or an item could not be
  supported. The incident is real; a person should look at the named fields.
- **`unresolved`** — nothing was established. No assertion, no confidence.

`Incident.validation.state` derives from these: accepted when something is grounded and nothing is open;
review_required when something is grounded but findings or open items exist; unresolved when nothing is grounded.
`unaccepted_fields()` lists the fields still carrying a non-accepted claim, `claim(field)` reads one field's provenance,
and `is_empty()` says whether anything was learned at all.

## 6. Evidence first

The model never mints an evidence id. It answers with a **quote** and the field it quotes from; the pipeline finds that
quote in the untouched field text, cuts the span, and derives the id. A claim whose quote is not in the source is
refused, not repaired.

- A quote that occurs more than once with no `char_start` to disambiguate is **not guessed** — it surfaces as
  `ambiguous_quote` and the claim stays unresolved. The prompt therefore asks for `char_start` on every quote.
- Record metadata is never textual evidence. The prompt forbids inventing a field named `metadata` or `record metadata`;
  a timestamp equal to the record's own `event_time` is answered with no field and no quote and is accepted as
  `SOURCE_METADATA` citing no span — the one exception to "accepted claims cite evidence", and an honest one: the record
  did carry that stamp.
- `title` and `description` may be grounded by repeating the record's own value; every enumerated field needs an
  explicit quote.
- A classification quote proves the event happened, it does not have to contain the token. `incident_type`,
  `category`, `department`, `severity`, `priority` and `event_status` are named for an event the record describes, and the
  record usually describes it in its own language, so `value` and `quote` almost never match literally. What stays refused
  is a token with no event sentence behind it — a topic, a heading, or the model's own expectation of such records.
- The event is read from the body, not the headline, and one span may support several fields.
- Provenance is copied from the record, never from the answer.

The contract then refuses what a model might have invented: `latitude`/`longitude` or `canonical_location_id` without
`resolution_state = resolved`; accepted items citing nothing; a value carried with no claim behind it; dangling evidence
references; duplicate ids; a relationship joining an item to itself; and `validation.state = accepted` while issues are
present — silent acceptance is a validator error, not a warning.

Findings carry one of `IssueCode`: `unsupported_claim`, `span_mismatch`, `invalid_value`, `unknown_field`,
`unauthorised_gis_value`, `unresolved_reference`, `missing_provenance`, `ambiguous_quote`, `provider_warning`.

`extraction/spans.py` stays the only sanctioned producer of `Evidence`, for the LLM path as it was for the deterministic
one.

## 7. Provider abstraction and configuration

`LLMProvider` is one abstract method — `complete(request) -> LLMResponse` — plus concrete `generate_text` and
`generate_structured`. Two implementations sit behind it and the pipeline knows neither: `GroqProvider` calls Groq
through the official `groq` SDK (`client.chat.completions.create`), and `OpenAICompatibleProvider` speaks the
chat-completions wire over stdlib `urllib` for OpenAI, vLLM, Ollama or any other compatible gateway. Which one runs is
configuration, not code. Both take an injectable client or transport, which is how the whole suite runs offline.

| Env var | Meaning |
| --- | --- |
| `INTELLIGENCE_LLM_PROVIDER` | `groq` for the official SDK; `openai_compatible` (aliases `openai`, `vllm`, `ollama`) for any other chat endpoint |
| `INTELLIGENCE_LLM_MODEL` | required once a provider is chosen |
| `INTELLIGENCE_LLM_BASE_URL` | endpoint override; each alias has its own default |
| `INTELLIGENCE_LLM_API_KEY` | never logged — `repr=False`, and `summary()` reports only `api_key_present` |
| `INTELLIGENCE_LLM_TIMEOUT_SECONDS` | per-request timeout |
| `INTELLIGENCE_LLM_MAX_RETRIES` | retried on 429/500/502/503/504 with linear backoff |
| `INTELLIGENCE_LLM_TEMPERATURE`, `INTELLIGENCE_LLM_MAX_TOKENS` | generation controls |
| `INTELLIGENCE_LLM_REASONING_EFFORT` | `none`/`default`/`low`/`medium`/`high`, sent to the endpoint only when set. A reasoning model spends its completion budget on thinking first, so this is the knob that decides how much of `MAX_TOKENS` reaches the answer |

An answer that the endpoint stopped early is never half-read: `complete()` raises `StructuredOutputError` naming
`INTELLIGENCE_LLM_MAX_TOKENS` and `INTELLIGENCE_LLM_REASONING_EFFORT` as soon as the endpoint reports
`finish_reason = "length"`, and a Groq 400 whose code is `json_validate_failed` — the schema-constrained answer never
arrived complete — carries the same two names.

No key is hardcoded anywhere, and none is ever rendered: `api_key` is excluded from `repr`, and `summary()` reports only
`api_key_present`. A Groq failure is reported as `TransportError` carrying the SDK error type, the HTTP status and the
message Groq sent — never the request, whose headers hold the credential — and authentication or permission failures say
so and are not retried; only genuinely transient ones are, which the SDK handles under `max_retries`. Because the SDK
adds `/openai/v1/chat/completions` itself, a base URL written in the OpenAI style (`…/openai/v1`) is reduced to its
origin before it reaches `Groq`. `python-dotenv` reads the repository's local `.env` before `os.environ` is consulted,
so the model endpoint can live in that file rather than in a shell; `load_dotenv(override=False)` means an exported
`INTELLIGENCE_LLM_*` variable always wins, and `LLMConfig.from_env(environ=...)` uses the mapping it is given and opens
no file at all — which is how the suite stays offline and leaves the process environment alone. A `.env` that does not
exist is simply nothing to load.

An incomplete environment raises `ProviderNotConfigured` naming the variable that is missing. There is no silent
fallback and no deterministic path pretending to be a model: with no provider configured the pipeline does not produce
an incident, and an empty answer produces an honest all-unresolved incident carrying only provenance.

Structured answers go as JSON Schema **strict mode** requests when the provider supports it. `GroqProvider` sends
`{"type": "json_schema", "json_schema": {"name": ..., "strict": true, "schema": ...}}` for any request that carries a
schema; `OpenAICompatibleProvider` keeps the plain `{"type": "json_object"}` hint, because vLLM and Ollama are not
uniformly strict-capable and a bare JSON object is still worth having there. Strict mode demands shapes Pydantic does
not emit by default, so `strict_json_schema` reshapes the draft schema for the wire: `$ref`s inlined, every object
`additionalProperties: false` with all its properties required, and optionality expressed as `null` unions instead of
omission. `default`, `title`-as-keyword, bounds, `pattern` and `format` are dropped — a property whose *name* is
`title` survives, since the field names are what is being described. Nothing here relaxes validation: a model that
answers in the wrong shape is still refused, and grounding stays a separate pass over the untouched record text.

## 8. Stage 1 deliberately does not

- **No new keyword or cue dictionaries.** No flood, crime, agriculture, severity, status, department or classification
  phrase lists; no hundreds of Tamil and English surfaces. Taxonomy values live only as enums in the output model, and
  the prompt's token lists are generated from those enums rather than maintained beside them.
- No FastAPI, no PostgreSQL, no GIS resolution. Coordinates and canonical places are GIS's to write; Intelligence leaves
  the resolution state pending and the write-back unset.
- No user-facing AI confidence score. Validation and review metadata is in the contract so a later stage can build on
  it and so nothing unsupported reaches a Collector's dashboard.
- No cross-record correlation, trends, briefing or chatbot.

## 9. The deterministic chain

Stages 1-8b of the previous design produced the same kind of result with rules and cue vocabularies. That work is **not
deleted**; it is being retired seam by seam as the LLM path takes over, not in one sweep.

| Kept and shared | Frozen — fix defects, do not extend |
| --- | --- |
| `models/base.py` — `StrictModel`, `Confidence`, evidence reference walking | `config/*.py` cue and surface tables |
| `models/evidence.py`, `models/enums.py` | `mapping/assembly.py`, `enrichment.py`, `classifier.py`, `operations.py`, `deduplication.py` |
| `extraction/spans.py` — span arithmetic and verification | `extraction/` language, normalisation, morphology and temporal readers |
| `mapping/record_input.py` — structural record reading | `models/` legacy schemas and correlation |

"Shared" is measurable, not remembered: an import audit of the five live-path modules reaches exactly these five legacy
files. Every other module under `models/`, `config/`, `extraction/` and `mapping/` is imported only by its own tests or
by another frozen module, so removing the deterministic chain later is a list of deletions rather than a search.
`LEGACY_DETERMINISTIC_SEAMS` in `__init__.py` names the frozen ones so a reader can tell which code is being replaced.
The legacy `Incident` stays under `intelligence.models` (schema 1.0) for the duration of the migration; the package root
exports only the new contract. The full design record of the deterministic chain — stage by stage, with the cue
statistics from the October 2026 capture — is in git history at commit `a6a5610`.

Determinism is retained where it belongs: schema validation, span replay, enum coercion, id assignment, timestamp
parsing, similarity calculations.

## 10. Later stages

| Stage | Work | Status |
| --- | --- | --- |
| 1 | LLM foundation, `Incident` contract, evidence-first validation, provider abstraction | **Complete** |
| 2 | Real extraction — call a live model over the capture, measure prompt quality, JSON compliance and recall | **In progress — shape conforms, recall tuning remains** |
| 2b | Runnable orchestration: `python -m intelligence.pipeline` reads the platform API and reports a batch | **Complete — read-only; posting results back is not built** |
| 3 | Entity and location resolution hand-off (GIS) | Planned |
| 4 | LLM classification and relevance | Planned |
| 5 | Severity, priority, operational status, department routing | Planned |
| 6 | Evidence hardening and revalidation when a source changes | Planned |
| 7 | Embedding deduplication and clustering | Planned |
| 8 | Recurring issues, trends, timelines | Planned |
| 9 | Cross-source correlation | Planned |
| 10 | "Why should the Collector care" and recommended actions | Planned |
| 11 | Collector briefing | Planned |
| 12 | OCR for image sources | Planned |
| 13 | Chatbot over validated incidents | Planned |

Deferred by decision: risk prediction, anomaly detection, sentiment, trend forecasting, a dedicated RAG stack, advanced
AI confidence scoring.

## 11. Hand-off to Alex

Integration uses this contract, not the extraction internals:

- Storable columns: `incident_id`, each scalar field, `event_time`, `provenance.*`, `validation.state`,
  `generation.model` / `prompt_version` / `input_hash`.
- `to_storage_document()` serialises the whole incident; `Incident.model_validate_json` reads it back with its
  validators intact.
- **View Source** survives extraction: `provenance.source_url` and `raw_reference` come through from the record, and
  every accepted claim's `evidence_ids` resolve to spans that replay against the stored field text.
- `review_required` is a human's to confirm. `unresolved` and `unknown` are non-authoritative — the absence of a
  finding, not a finding of absence.
- The API code here is a read-only client: `pipeline.py` `GET`s records and prints what the chain made of them. It never
  posts an Incident, never writes to PostgreSQL and never connects to the database itself.

## 12. Tests and verification

```bash
python -m pytest intelligence/tests -q      # 869 passed
python -m compileall intelligence           # clean
```

Stage 1 adds 88 tests: `test_contract.py` (14) on the model rules — what a legal incident must carry and what it must
refuse; `test_llm.py` (30) on configuration from the environment and from a local `.env`, precedence, secret
non-exposure, retries, both provider clients and their error mapping, JSON parsing, the strict-mode schema a model
is shown, the reasoning-budget setting and a completion that ran out of tokens; `test_pipeline.py` (26) on the record →
incident path with a scripted provider, covering invented quotes,
out-of-taxonomy tokens, handed-in coordinates, ambiguous spans, ungrounded locations, empty answers, the three source
shapes, a price grounded on its own number, metadata that is not quotable text, a fraud raid classified from the event
it describes rather than from an English token in the text, the ceiling an over-long item list is cut to, a second pass
over the same record, and what the prompt is allowed to say;
`test_orchestration.py` (18) on the runnable path — the backend URL from the environment, `GET /records` and
`GET /records/{record_id}`, the safe default limit, malformed and erroring responses, unreachable backend, per-record
failures that do not stop a run, the classification of a result as incident / context / review / failure, the console
line and the CLI's refusals. The 781 pre-existing deterministic tests still pass unchanged.

### Run it

```bash
python -m intelligence.pipeline                    # the safe default: five records
python -m intelligence.pipeline --limit 20
python -m intelligence.pipeline --record-id WEATHER-MDU-03-Oct
python -m intelligence.pipeline --all --json       # every record, full Incident documents
```

The batch reads `INTELLIGENCE_API_BASE_URL` (default `http://127.0.0.1:8000`) and provider settings from
`INTELLIGENCE_LLM_*`, in the environment or in the repository `.env`; nothing is hardcoded. It prints the backend URL,
how many records were read and processed, then one line per result — id, title, incident type, category, department,
severity, priority, event status, event time and the review state with its finding count — grouped into incidents,
contextual records and failures, with a summary and a list of ids that need a person. Exit codes are 0 when every
record produced an Incident, 1 when some failed, 2 for a backend failure and 3 when no provider could be built. A run
with no argument never reads more than the default five records, nothing is posted back, no credential is ever printed,
and a record whose model call fails is reported while the rest of the batch continues.

All 1,059 records of the October 2026 Madurai capture (`ingestion/data/normalized`: 1,004 agriculture, 35 news, 20
weather) were pushed through `build_context` and `extraction_request` without a model — no failures, 0.20 s total, and
each source reached the prompt with its own fields: `data.warning`/`data.max_temp_c` for IMD,
`data.commodity`/`data.min_price` for the markets, `data.content` for Dinamalar.

Groq has been called for real through the full chain on all three shapes. The IMD warning line and a sapota price line
came back schema-shaped, with `char_start` on every quote, and validated `accepted` with **zero issues** — and neither
invented a severity, priority or incident type: the price record's incident fields all arrived `null`, and its
`event_time` was answered with no field and no quote, taking the metadata path. A full Tamil article answers in shape
too, but needs more completion room than this account's tier allows (§13).

## 13. Current limitations

- **A full Tamil article needs its completion budget managed.** Strict mode makes all 44 answer properties required and
  `openai/gpt-oss-20b` spends part of the budget reasoning before it emits JSON, so a Dinamalar record — ≈4.3k prompt
  tokens (7.4k chars of rules and taxonomy, 2.6k chars of Tamil text) against an 8,000-tokens-per-minute `on_demand`
  limit — answers `json_validate_failed` with an empty `failed_generation` whenever reasoning plus answer exceed
  `INTELLIGENCE_LLM_MAX_TOKENS`. Weather and market records — 0.5-1k prompt tokens — extract reliably. Three generic
  levers hold this open, none of them a source special case: the prompt states and `validate_extraction` enforces a
  ceiling of 8 items per list (`MAX_ANSWER_ITEMS`), because "name every place and person" is otherwise an unbounded
  answer; `INTELLIGENCE_LLM_REASONING_EFFORT=low` moves budget from reasoning to the answer; and a truncation now names
  both knobs instead of arriving as an opaque 400. Raising the tier or `INTELLIGENCE_LLM_MAX_TOKENS` remains the user's
  call — `process_record` propagates the failure and invents nothing.
- **Recall is now the open question, not the shape.** Under strict-mode structured outputs every scalar comes back as a
  `GroundedValue` object and `validate_extraction` builds an incident instead of raising. What stage-2.3/2.4 of the
  prompt changed is the model's licence to classify: a Tamil article previously yielded only `department`, because the
  rules read as "the quote must contain the English token". The rules now require a classification to be read from the
  event the body states and cap each item list at 8. Precision mechanisms are untouched, so a run that over-claims shows
  up as `span_mismatch`/`invalid_value` findings, and one that abstains shows up as `unresolved` fields — both measurable
  per record with `python -m intelligence.pipeline --record-id <id>`.
- One record in, one incident out. No cross-record view yet (Stages 7, 9).
- The batch path ends at the console. Nothing is posted back to the platform, so `python -m intelligence.pipeline` is a
  development and verification tool until an endpoint or a job runner takes the Incidents it prints. `--limit` is applied
  client-side because `GET /records` has no paging, records are processed one after another, and a rate-limited provider
  makes the rest of a large `--all` run fail per record — reported, not retried.
- Grounding is quote-only, so a correct claim the model cannot quote verbatim is refused. Recall will sit below the
  deterministic path until the prompt is tuned; precision is the deliberate priority.
- Language and normalisation handling is not wired into the LLM path yet — record text reaches the model untouched, so
  Tanglish, heavily inflected Tamil surfaces and a news page's navigation furniture are the model's problem to solve.
- Two `Incident` contracts coexist during the migration.
- `relationships` bind by exact text match, so a paraphrased subject names nothing and is recorded unresolved.
- A mis-grounded `event_time` that happens to equal the record's own stamp is accepted as metadata; the deterministic
  design's rule that a news article's publication time is not its event time is not re-enforced here, and Stage 2 has
  to watch for it.
