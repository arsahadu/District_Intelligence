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
| `records.py` | The record seam: the only place that knows how a CommonRecord is spelled — `read_record`, `RecordInput`, `input_hash`, the field paths, the read errors |
| `spans.py` | Span arithmetic and verification: `SourceField`, `Span`, `find_spans`, `locate`, `build_evidence`, `verify_evidence`, `revalidate` |
| `models/` | Shared pydantic vocabulary: `base.py` (`StrictModel`, `Confidence`), `enums.py` (the controlled taxonomies), `evidence.py` (`Evidence`) |
| `tests/` | The suite: contract, provider, pipeline and orchestration tests over scripted providers and record fixtures, plus the span and evidence tests (§12) |

## 4. The Incident contract

`contract.Incident` is what the platform persists. `schema_version = "2.0"`.

| Section | Holds |
| --- | --- |
| `incident_id`, `provenance` | `INC-<record_id>` and the whole source trail: record id, source id/type, record type, `source_url`, `raw_reference`, `retrieved_at`, modality, language/district/state hints, declared severity and status, `input_hash` |
| `title`, `description` | what the record printed, grounded by echoing it, plus the account whose quote proves it |
| `incident_type`, `category`, `department`, `severity`, `priority`, `event_status` | controlled taxonomy tokens, or `unresolved` / `unknown` |
| `record_kind`, `context_type` | what the record *is* — `incident`, `forecast`, `context`, `unresolved` — and for the middle two the word for what it carries: `market_price`, `weather_forecast`, `administrative_notice`, `general_information`, `other` |
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

`record_kind` is what lets a sapota price and a flood share one contract without either being a lie. Context is a
record's value, not a rejected case: a market line or an IMD forecast that states no event comes through as
`record_kind=context`/`forecast` with a `context_type`, and it is answered with nulls for the event fields — which land
as the contract's own `unresolved`/`unknown`, the absence of a finding rather than a finding of absence. It is not
forced into a hollow incident whose severity and description a person would then be asked to fix. The kind is decided by
what was evidenced, never by what the answer called itself, and an evidenced happening outranks the values the same
record also carries (§6). What such a record does carry is kept: `context_facts`
holds up to eight of the record's own field values — a commodity, a price, a warning line, a temperature — each one the
words its named field really holds, cited by a span in that same field. There is no `MarketPriceContext` or
`WeatherContext` class: the field path is the record's, so a feed nobody has met yet needs no new code.

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
- The field a quote names is part of the evidence, not a label. A body sentence cited on `title` fails the replay and the
  finding names the field that really carries it, so a wrong `field` is refused and diagnosed rather than repaired.
- One span cannot be a place and a party at once. When the same span is cited by a `locations` item and an `entities`
  item, the record gets a `provider_warning` and goes to review — the confusion is reported, never silently chosen.
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
- What a record **is** comes from what was evidenced, not from what the answer asserted about itself. Only an established
  event makes an incident, and a bare declaration can never manufacture one: an answer that files a record as `incident`
  while no quote supported an event *or* an account of one is demoted — to the `context` the same answer offered, or to
  `unresolved` — under a `provider_warning`, and an answer that labels a record `forecast`/`context` while one of its own
  quotes established an event has that label dropped, also said out loud. The routes are deliberately unequal, and an
  occurrence always outranks a value: a grounded `description` carries an event when the answer read the record as
  happening — by its own `incident` label, or by an `event_status` that only a happening can carry, since ongoing, under
  investigation, action taken, resolved and reported say nothing about a price, a temperature or a schedule — while a
  quoted price in `description` with no status behind it never promotes a market line, and `context_facts` never tip the
  decision either way. And the kind is not held hostage downstream — an incident whose `incident_type` no quote could
  name keeps `record_kind=incident` on its account and goes to review for the type, because one unreadable field does not
  undo an evidenced happening.
- A record that states no event still states values, and they are kept. `context_facts` carries up to eight of the
  record's own field values, each with the span it was cut from. Two deterministic checks back the model here: the quote
  replays in the field it names exactly as every other quote must, and the `value` must be what that field really holds —
  or that field's own number, so `1,004` survives a field printed `1004.0` while `₹60 per kilogram` offered for a field
  holding `60.0` does not. A fact that fails the second check keeps its evidence and goes to review with an
  `unsupported_claim` naming the field; it is never quietly reworded. Nothing source-shaped is in the schema, so a feed
  nobody has met needs no new code.
- An incident owes the dashboard its account. A record read as an event whose own text grounded no `description` is not
  accepted: the missing account is a `provider_warning` finding on `description` and the result goes to review. Same
  discipline for `event_status` — an incident whose text never said whether it is still happening is filed `unknown`
  *with a generation warning*, so `unknown` stays the pipeline's statement that the source did not say, never a blank
  that reads as a solved field. `event_time` and `priority` are said out loud the same way when an incident carries
  neither: the first is not filled from the retrieval stamp and the second is not copied down from the severity, and both
  warnings sit on the result without demoting a record whose evidenced fields are clean.
- A number the model never gave is the pipeline's, and is labelled as one. Empty confidence becomes
  `DEFAULT_LLM_CONFIDENCE = 0.5`, and that attribution now sits on the `Evidence` row's `notes` as well as in
  `generation.warnings`, so a quote that was found is never mistaken for a quote the model rated. Nothing defaults to
  1.0.
- The event is read from the body, not the headline, and one span may support several fields.
- Provenance is copied from the record, never from the answer.

The contract then refuses what a model might have invented: `latitude`/`longitude` or `canonical_location_id` without
`resolution_state = resolved`; accepted items citing nothing; a value carried with no claim behind it; dangling evidence
references; duplicate ids; a relationship joining an item to itself; and `validation.state = accepted` while issues are
present — silent acceptance is a validator error, not a warning.

Findings carry one of `IssueCode`: `unsupported_claim`, `span_mismatch`, `invalid_value`, `unknown_field`,
`unauthorised_gis_value`, `unresolved_reference`, `missing_provenance`, `ambiguous_quote`, `provider_warning`.

`spans.py` is the only sanctioned producer of `Evidence`: every quote reaches the contract through its offset arithmetic
and its replay against the field it was cut from.

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

## 8. Deliberate non-goals

- **No new keyword or cue dictionaries.** No flood, crime, agriculture, severity, status, department or classification
  phrase lists; no hundreds of Tamil and English surfaces. Taxonomy values live only as enums in the output model, and
  the prompt's token lists are generated from those enums rather than maintained beside them.
- No FastAPI, no PostgreSQL, no GIS resolution. Coordinates and canonical places are GIS's to write; Intelligence leaves
  the resolution state pending and the write-back unset.
- No user-facing AI confidence score. Validation and review metadata is in the contract so a later stage can build on
  it and so nothing unsupported reaches a Collector's dashboard.
- **No rule repairs an answer.** Nothing here fills a field from a phrase list, from a lookup on `source_type` or
  `record_type`, or from a default that would read as a finding. Accuracy comes from the model's reading of the record
  and stays only as far as its own quote backs it; where the quote is missing the honest answer is `unresolved`, said
  out loud, not a plausible value.
- No cross-record correlation, trends, briefing or chatbot.

## 9. What is deterministic

The rule-based chain the earlier stages built is **gone**: the cue and surface tables under `config/`, the language,
normalisation, morphology, place, actor, severity, status and temporal readers under `extraction/`, the
assembly/classification/enrichment/operations/dedup seams under `mapping/`, and the schema-1.0 models they produced. An
import audit of the live path — `pipeline.py`, `intelligence.py`, `context.py`, `contract.py`, `llm.py` and everything
they reach — found none of them reachable: each was imported only by its own tests or by another frozen module. The LLM
path had already taken over every decision they made, so removing them took no capability with it, and their tests left
with them.

Five modules the audit *did* reach stayed, because the LLM path runs on them: `models/base.py` (`StrictModel`,
`Confidence`, evidence reference walking), `models/enums.py` (the controlled taxonomies), `models/evidence.py`
(`Evidence`), and the two shared helpers it found at the bottom of the frozen tree — the span arithmetic, now
`spans.py`, and the record reader, now `records.py`. The full design record of what was retired — stage by stage, with
the cue statistics from the October 2026 capture — is in git history at commit `a6a5610`.

What is deterministic now, and should stay so: schema validation, span replay against the source text, enum coercion,
id assignment, timestamp parsing, record reading and hashing. Semantics come from the model; no rule fills a field the
model left empty.

## 10. Later stages

| Stage | Work | Status |
| --- | --- | --- |
| 1 | LLM foundation, `Incident` contract, evidence-first validation, provider abstraction | **Complete** |
| 2 | Real extraction — call a live model over the capture, measure prompt quality, JSON compliance and recall | **In progress — shape conforms, recall tuning remains** |
| 2b | Runnable orchestration: `python -m intelligence.pipeline` reads the platform API and reports a batch | **Complete — read-only; posting results back is not built** |
| 2c | Extraction accuracy: `record_kind`/`context_type`, semantic enum reading from a Tamil span, status/account/severity discipline, honest confidence | **Complete in the LLM path — a live recall measurement over the capture remains** |
| 2d | Semantic reasoning: kind independent of the fields after it, three-question classification, status/severity/priority discipline, generic `context_facts` | **Complete in the LLM path — the live measurement covers 2c and 2d together** |
| 2e | Event-vs-context ordering: the occurrence question asked before any label, an evidenced happening outranking quoted values, facts kept to data points | **Complete in the LLM path — the live run of 2e is what decides it** |
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

- Storable columns: `incident_id`, each scalar field, `event_time`, `record_kind` / `context_type`, `provenance.*`,
  `validation.state`, `generation.model` / `prompt_version` / `input_hash`. `record_kind` is what a dashboard filters on
  to keep price lines and forecasts out of an incident list without dropping them. `context_facts` is a list of
  `{fact_id, field, value, confidence, review, evidence_ids, notes}` rows — the field path is the record's own, so the
  column needs no per-source schema and a new feed's fields need no migration.
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
python -m pytest intelligence/tests -q      # 199 passed
python -m compileall intelligence           # clean
```

199 tests cover everything the package now runs. The 133 on the LLM chain are: `test_contract.py` (19) on the model rules — what a legal incident must carry and what it
must refuse, including that `record_kind=incident` cannot survive an unestablished event, that one unreadable
classification field does not demote an evidenced account of what happened, that context cannot be stated while a quote
establishes one, that a `context_type` rides nothing but a forecast or a context record, and that a `ContextFact` is a
quoted value — refused accepted-without-evidence, refused for a dangling evidence id, refused for a duplicate id, and
listed by `unaccepted_fields()` like any other open item;
`test_llm.py` (30) on configuration from the environment and from a local `.env`, precedence, secret
non-exposure, retries, both provider clients and their error mapping, JSON parsing, the strict-mode schema a model
is shown, the reasoning-budget setting and a completion that ran out of tokens; `test_pipeline.py` (66) on the record →
incident path with a scripted provider, covering invented quotes,
out-of-taxonomy tokens, handed-in coordinates, ambiguous spans, ungrounded locations, empty answers, the three source
shapes, a price grounded on its own number, metadata that is not quotable text, a fraud raid classified from the event
it describes rather than from an English token in the text, a Tamil flood classified from its own sentence with a
restated description, a body quote wrongly named as the title (refused, and the finding says which field really carries
it), mixed Tamil/English mentions, one span claimed as both a place and a party, a forecast left as context, the
ceiling an over-long item list is cut to, a second pass
over the same record, and what the prompt is allowed to say — for stage 2.6, a Tamil police probe whose verb
supplies both the department and `under_investigation`, a Tamil investment fraud read as a genuine incident with an
account its body supports, a normalised place name refused the moment it stops being the source's own words, each
controlled token read from the meaning of a Tamil sentence rather than from a literal English surface, a `severity`
nobody quoted for staying `unresolved` however likely it looks, lives lost in the text as the evidence a band is answered
from, the four status verbs the source uses in the present tense, an administrative act the record states as having
happened, a diary of programmes that have not happened as context, an account read from the body citing the body and
never the headline, an incident nobody could describe going to a person instead of passing as accepted, an answer that
cannot file itself as an incident no quote established, weather text that reports damage read as an incident even where
the record called it a forecast, and a number the model never reported being attributed to the pipeline on the claim
*and* on the evidence row; and for stage 2.7, a sapota price line keeping five of its own field values with every fact
replaying from the field it names (`1,004` accepted for a field printed `1004.0`), an IMD warning keeping its warning
line and its two temperatures as the same generic fact type, a fact whose value its field does not hold keeping its
evidence but going to review under `unsupported_claim`, a fact naming a field the record does not print citing
nothing, accepted facts with no `record_kind` answered being filed `context` by the evidence rather than by the feed,
a genuine Tamil flood whose refused `incident_type` quote costs it the type and not the kind, the same flood with no
type answered at all landing on a `provider_warning` that says the kind stands, and an incident missing both its clock
and its urgency saying why each stayed null while still passing as accepted; and for stage 2.8, a Tamil
economic-offences probe the answer had filed as an administrative notice being read back as an incident on its own
account and its own `under_investigation` while its quoted value stays a fact, the same record with no `record_kind`
answered at all still an incident and never derived into context by its facts, an incident whose place quote was
invented and whose severity and priority were never stated keeping the kind, a price line and an IMD forecast whose
prose was summarised into `description` keeping `context` and `forecast` because no happening was quoted, an answer
that declared an incident from three quotes absent from the record being demoted with nothing accepted, and the rules
block asking whether something happened before it asks for a label;
`test_orchestration.py` (18) on the runnable path — the backend URL from the environment, `GET /records` and
`GET /records/{record_id}`, the safe default limit, malformed and erroring responses, unreachable backend, per-record
failures that do not stop a run, the classification of a result as incident / context / review / failure, the console
line including its `facts=` count and the contextual block's note, and the CLI's refusals. The other 66 cover the two
shared helpers underneath them: `test_spans.py` (54) on the offset arithmetic — quoting, locating, empty and ambiguous
quotes, verification and revalidation — and `test_evidence.py` (12) on the `Evidence` model's own rules. The frozen
chain's tests left with the chain (§9).

### Run it

```bash
python -m intelligence.pipeline                    # the safe default: five records
python -m intelligence.pipeline --limit 20
python -m intelligence.pipeline --record-id WEATHER-MDU-03-Oct
python -m intelligence.pipeline --all --json       # every record, full Incident documents
```

The batch reads `INTELLIGENCE_API_BASE_URL` (default `http://127.0.0.1:8000`) and provider settings from
`INTELLIGENCE_LLM_*`, in the environment or in the repository `.env`; nothing is hardcoded. It prints the backend URL,
how many records were read and processed, then one line per result — id, title, record kind, incident type, category,
department, severity, priority, event status, event time, the count of kept field values when a record carried any and
the review state with its finding count — grouped into
incidents, contextual records and failures, with a summary and a list of ids that need a person. A record in the second
group is not a bad extraction: `kind=forecast` or `kind=context` says the pipeline read the record for what it states and
found no event in it, which is the record's value rather than a rejected case, and `facts=5` on that line says the values
it does state survived into the result. Exit codes are 0 when no record failed —
a contextual result is a processed record, not a failure — 1 when at least one record could not be processed, 2 for a
backend failure and 3 when no provider could be built. A run
with no argument never reads more than the default five records, nothing is posted back, no credential is ever printed,
and a record whose model call fails is reported while the rest of the batch continues.

All 1,059 records of the October 2026 Madurai capture (`ingestion/data/normalized`: 1,004 agriculture, 35 news, 20
weather) were pushed through `build_context` and `extraction_request` without a model — no failures, 0.35 s total, and
each source reached the prompt with its own fields: `data.warning`/`data.max_temp_c` for IMD,
`data.commodity`/`data.min_price` for the markets, `data.content` for Dinamalar. Every one of them is shown the same
rules: the instruction block is 14.4-14.5k chars whatever the source — 10,448 of it the rules — and the whole request
runs about 14.9k chars for a weather record, 15.1k for a price line and 18.3k for the largest article, so the
incident/forecast/context decision, the three-question classification and the `context_facts` instruction are made by one
set of words for a price line and a flood alike.

Groq has been called for real through the full chain on all three shapes. The IMD warning line and a sapota price line
came back schema-shaped, with `char_start` on every quote, and validated `accepted` with **zero issues** — and neither
invented a severity, priority or incident type: the price record's incident fields all arrived `null`, and its
`event_time` was answered with no field and no quote, taking the metadata path. A full Tamil article answers in shape
too, but needs more completion room than this account's tier allows (§13). Under stage 2.7 the same three live records
kept their values — the price line and the forecast arrived as `context` and `forecast` with `context_facts` their fields
really hold — while the Tamil article on an investment fraud, a registered case and a search arrived as
`context`/`administrative_notice` with two facts, its title and its body: the label won over the evidence, which is what
stage 2.8 fixes (§6, §13). Those live runs were made against the stage-2.5 and stage-2.7 prompts; the kind ordering, the
status-verb rule and the account requirement are verified by the scripted-provider suite, and the first live measurement
of stage 2.8 is a `python -m intelligence.pipeline` run away.

## 13. Current limitations

- **A full Tamil article needs its completion budget managed.** Strict mode makes all 54 answer properties required and
  `openai/gpt-oss-20b` spends part of the budget reasoning before it emits JSON, so a Dinamalar record arrives at the
  endpoint as 18.3k chars for the largest article in the capture — a 14.5k-char rules-and-taxonomy block that every
  record pays, plus 3.8k chars of Tamil text on top. (The same prompt at 10k chars measured ≈4.3k tokens against an
  8,000-tokens-per-minute `on_demand` limit, so this is on the order of 7.8k prompt tokens.) Such a record answers
  `json_validate_failed` with an empty `failed_generation` whenever reasoning plus answer exceed
  `INTELLIGENCE_LLM_MAX_TOKENS`. Weather and market records add only a few hundred chars of their own and land at
  14.9-15.1k, and extract reliably. Three generic
  levers hold this open, none of them a source special case: the prompt states and `validate_extraction` enforces a
  ceiling of 8 items per list (`MAX_ANSWER_ITEMS`), because "name every place and person" is otherwise an unbounded
  answer; `INTELLIGENCE_LLM_REASONING_EFFORT=low` moves budget from reasoning to the answer; and a truncation now names
  both knobs instead of arriving as an opaque 400. Raising the tier or `INTELLIGENCE_LLM_MAX_TOKENS` remains the user's
  call — `process_record` propagates the failure and invents nothing.
- **Recall is now the open question, not the shape.** Under strict-mode structured outputs every scalar comes back as a
  `GroundedValue` object and `validate_extraction` builds an incident instead of raising. What stage-2.3/2.4 of the
  prompt changed is the model's licence to classify: a Tamil article previously yielded only `department`, because the
  rules read as "the quote must contain the English token". Stage-2.5 targets the rest of the quality list, all of it
  generic: the rules separate an event from a forecast, a price line, an announcement and an appeal before anything is
  classified; they require each quote to name the field it copies and forbid citing the title for what the body says;
  they allow `description` to be a shorter restatement as long as its quote points at the field carrying the words; they
  require `char_start` always; and they decide a name's list from the words around it, not from its sound. Two
  deterministic aids back this without loosening anything: a `span_mismatch` finding now names the field that really
  carries the quote, and one span cited as both a location and an entity raises a `provider_warning` and the record goes
  to review.
  Stage-2.6 targets the opposite failure — real news coming back `unresolved` on every field while a price line arrived
  as an empty incident — again without touching a precision rule. Prompt side: rule 5 says what a record *is* before it
  asks what happened, which frees a plain event from a "must be dramatic" reading and makes `personnel_transfer`,
  `announcement_only` and `ceremonial_or_award_event` reachable at all (the previous wording sent administrative acts to
  context, where those tokens could never be answered); rule 4 names the five status readings and reserves `unknown` for
  a text that says nothing about now; rule 6 tells the model that a record it called an incident almost always carries an
  account; rule 7 separates a place's cleaned name from its evidence; rule 10 ties `confidence` to the reading, not to
  the find. The deterministic half is §6: kind derived from evidence, a missing account made a finding, an ungrounded
  status said out loud, an unreported number attributed. Expected consequence, stated plainly rather than flatteringly:
  fewer `incident_type=unresolved` news records and more grouped as context or forecast, and possibly *more*
  `review_required`, because an answer naming an event without quoting an account for it is now reported instead of
  accepted. Precision mechanisms are untouched, so a run that over-claims shows
  up as `span_mismatch`/`invalid_value` findings, and one that abstains shows up as `unresolved` fields — both measurable
  per record with `python -m intelligence.pipeline --record-id <id>`.
  Stage-2.7 takes the two failures the live path was expected to have left: a real event demoted because one field after
  it could not be read, and a context record whose useful numbers were thrown away. Prompt side: rule 3 splits
  `incident_type`/`category`/`department` into three questions about one event and says one sentence may carry all three;
  rule 4 refuses `info` as a softer guess, states that a subject being police, fraud or disaster carries no band, says
  priority is read from urgency and may be null, and forbids taking a status from the feed or from the kind of event;
  rule 5 says the kind is settled by the event and not by the fields that follow it, and asks for `context_facts` as
  copies of the record's own fields; rule 11 says to abstain one field at a time. Deterministic side: an evidenced
  account carries `record_kind=incident` when the type token fails (§6), accepted facts with no kind answered derive
  `context`, and `ContextFact` carries a value only with the span its own field holds. The expected live consequence is
  again more review rather than less: an incident that keeps its kind without its type now arrives as
  `review_required` with a finding naming the type, which is the honest shape of "the event is real, the taxonomy is
  nobody's guess".
  Stage-2.8 answers the one live result stage 2.7 got wrong: a Dinamalar economic-offences article — money taken on a
  promise, a case registered, a wing still investigating, material seized — filed `record_kind=context` with
  `context_type=administrative_notice` and its title and body kept as two facts. Prompt side: rule 5 now asks the
  occurrence question first (did an actor do something, was something done to someone, did a state change), names an
  investigation, a search, a seizure or a registered case as a happening whoever wrote it up, forbids filing a happening
  as context because no token fits, and forbids blanking an event's fields once the record was read as one; a fact is
  one data point a field holds, never a headline and never the body's prose; rule 4 counts searching into a matter as
  investigating and says a record with no happening has no status to report. Deterministic side: an evidenced
  occurrence outranks quoted values, because a grounded account carries the event on a status of a happening as well as
  on the answer's own `incident` label — five tokens that presuppose something took place, which no price, temperature,
  station id or schedule can carry — while facts alone still decide nothing. A record labelled `incident` whose every
  event quote is invented is still refused, and a price line or a forecast summarised into `description` with no status
  behind it still stays context or forecast.
- **Kind is a shape decision, not a claim, so it carries no quote of its own.** `record_kind` and `context_type` are
  bare tokens in the answer, and the contract enforces them one way only: an evidenced event outranks a context label,
  and a declaration can never create an event. That means a model which answers every field well but leaves
  `record_kind` out gets `unresolved` as its kind — the warning says so — and the pipeline does not infer a kind from
  `source_type`, because a news feed carries events and an agriculture feed can carry a flood. Since stage 2.7 the same
  omission lands as `context` when the answer's accepted evidence is field values and nothing evidenced an event, which
  is still the evidence deciding, not the feed. Since stage 2.8 the omission can also land as `incident`, when the
  answer grounded an account of the happening *and* a status of a happening: that route always leaves the record
  `review_required`, because the type token behind the kind is what a person is being asked to confirm.
- **A context fact is a copy, never a reading.** `value` must be what the named field holds (or that field's own number),
  so a price the record states as `46.0` cannot arrive as `₹46 per kg` and a fact is silently less useful than a
  hand-written summary would be. That is the trade taken on purpose: the only thing this pipeline may say about a
  market line or a forecast is what its fields already say. Up to eight survive (`MAX_ANSWER_ITEMS`), the same ceiling
  every other list pays, so a wide table is truncated with a finding rather than answered past the completion budget.
- **Statuses are read, not conjugated.** `under_investigation`, `action_taken` and `resolved` come from the model's
  understanding of the source's own verb, so an article whose verb it misses lands as `unknown` with a warning. The rule
  tables that used to read those verbs are gone (§9) and nothing is wired in to catch the miss; the live measurement
  decides whether that is a real gap or a cheap one.
- One record in, one incident out. No cross-record view yet (§10).
- The batch path ends at the console. Nothing is posted back to the platform, so `python -m intelligence.pipeline` is a
  development and verification tool until an endpoint or a job runner takes the Incidents it prints. `--limit` is applied
  client-side because `GET /records` has no paging, records are processed one after another, and a rate-limited provider
  makes the rest of a large `--all` run fail per record — reported, not retried.
- Grounding is quote-only, so a correct claim the model cannot quote verbatim is refused. Recall depends on how the prompt
  is worded; precision is the deliberate priority.
- Nothing stands between the record and the model: no normalisation, boilerplate stripping or transliteration step runs
  first, so Tanglish, heavily inflected Tamil surfaces and a news page's navigation furniture are the model's problem to
  solve.
- `relationships` bind by exact text match, so a paraphrased subject names nothing and is recorded unresolved.
- A mis-grounded `event_time` that happens to equal the record's own stamp is accepted as metadata; nothing here
  re-enforces the rule that a news article's publication time is not its event time, and the live run has to watch for
  it.
