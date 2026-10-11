# District Intelligence — Intelligence Module

## Purpose

The AI/NLP layer of the Collector's District Intelligence Platform. It reads source-independent `CommonRecord` objects
and returns evidence-grounded `Incident`s: every field the pipeline asserts carries the quote it came from, or it says it
does not know.

**Intelligence lives entirely inside `intelligence/`.** It imports nothing from `ingestion/`, `backend/`, `frontend/` or
GIS — it reads records by declared field path against a structural protocol, so a renamed or missing field fails loudly
instead of surfacing as a silent `None`. Where it talks to the platform it uses HTTP: the orchestrator `GET`s
CommonRecords and, only when explicitly invoked with `--post-incidents`, `POST`s the accepted Incidents, forecasts and
context records back. It never opens a database connection.

| Owner | Responsibility |
| --- | --- |
| Ahad — Data Integration | Source → `CommonRecord` |
| Prem — Intelligence | `CommonRecord` → validated `Incident` (this module) |
| Alagan — GIS | Place mentions → canonical geography, coordinates, visualisation |
| Alex — Platform | `Incident` → PostgreSQL, FastAPI, Collector workspace |

## Pipeline

```
Platform API  GET /records                    — one CommonRecord per object, read once per run
  ↓
pipeline.run_pipeline                         — internal batches of --batch-size, progress flushed at each
  ↓                                             batch boundary; one reader per run, built only if a narrative
  ↓                                             record still has to be read
pipeline.process_record                       — the one-record boundary
  ↓
router.route                                  — a row of field values, or prose to be read for meaning?
  ↓ STRUCTURED                        ↓ SEMANTIC
structured.process_record            intelligence.extract_incident
  │                                  ↓
  │                        context.build_context — records.read_record, then every field the
  │                        record carries, citable by its own path
  │                        ↓
  │                        intelligence.extraction_request — one prompt: the fields + the taxonomy
  │                        ↓
  │                        fallback.Chain.read — the configured model first, the next candidate only
  │                        ↓                  if a refusal says that model was the problem
  │                        LLMProvider.generate_structured — Gemini, the groq SDK, or any
  │                                             OpenAI-compatible endpoint
  │                        ↓  ExtractionDraft (all-optional wire model, extra keys ignored)
  │                        intelligence.validate_extraction → _assemble — span replay, coercion, ids
  └────────────┬───────────┘
               ↓
geography.source_geography        — what the record declared about place, as source values
               ↓
geography.source_locations        — on a value-shaped record, the declared places a Location can hold, pending GIS
               ↓
contract.Incident (schema 2.0)    — claims + evidence + validation + generation, one shape for both readers
```

Entry point: `python -m intelligence.pipeline`. Per-record boundary: `pipeline.process_record(record)`.

By default the CLI reads and reports only. Passing `--post-incidents` opts in to posting the run's **accepted** records to
`POST /incidents` — incidents, IMD forecasts and market or agriculture context, both the ones read this run and the ones
reused from a checkpoint, since a resumed run has results to write back even when it asked no model anything;
`INTELLIGENCE_API_BASE_URL` configures the API origin and defaults to `http://127.0.0.1:8000`. Review-required, deferred,
failed and `unresolved`-kind results are never posted, an incident id that reaches the posting step twice is sent once, a
duplicate the backend already holds is reported as a duplicate and still exits `EXIT_OK`, and API validation/server errors
and connection failures are reported explicitly with exit code `EXIT_POST`. `RunSummary.postable` is that selection, gated
by `pipeline.POSTABLE_KINDS`, so what a run may write back is one property rather than a rule the caller has to remember.
A forecast and a price line go back under their own `record_kind` and `context_type` with their event-shaped fields left
`unresolved`/`unknown`, because nothing in the record evidenced an event — the endpoint is shared, the classification is
not. Each line names the kind it sent (`forecast INC-WEATHER-MDU-03-Oct created (HTTP 201)`), and a run that produced
nothing postable says so in one line and still exits `EXIT_OK`.
`context.build_context`, `intelligence.extraction_request`, `intelligence.extract_incident` and
`intelligence.validate_extraction` are callable directly for stage-by-stage testing.

**One chain, three sources.** `data` is source-specific while the CommonRecord around it is not, so the context builder
discovers the keys a record actually carries instead of assuming a news-shaped one: a Dinamalar article offers
`data.content`, an IMD record `data.warning`/`data.forecast` and its temperatures, a market line `data.commodity`,
`data.market` and its prices. Strings and rendered numbers both become citable fields, so a quote of `50.0` from
`data.max_price` grounds exactly like a Tamil sentence. There is no `NewsPipeline`/`IMDPipeline`/`AgriPipeline` and no
field list per `source_type` — `source_type` and `record_type` reach the model as context, and the prompt tells it that
a field another source usually carries does not exist here unless it is printed.

| File | Responsibility |
| --- | --- |
| `records.py` | The record seam — the only place that knows how a CommonRecord is spelled: `read_record`, `input_hash`, field paths, read errors, and whether `event_time` arrived as a day or as a moment |
| `context.py` | `RecordContext`: every field the record carries as a citable `SourceField`, its hash, prompt block and provenance, plus its `location` slot kept out of the prompt |
| `router.py` | `Mode`, `route` — which reading one record gets |
| `structured.py` | The value reader: copies each field into a `context_fact`, keeps the day a record stated as a day (`event_time_precision`), hands its declared geography to `source_locations`, files the record as `context`/`forecast`. No provider import, no prompt, no quota |
| `intelligence.py` | The semantic engine: request building, draft parsing, grounding, assembly |
| `llm.py` | `LLMProvider`, `LLMConfig`, `LLMRequest`/`LLMResponse`, `GeminiProvider`, `GroqProvider`, `OpenAICompatibleProvider`, `build_provider`, and the refusals read off what the endpoint answered: `QuotaExhaustedError` (allowance spent) vs `RateLimitedError` (window busy) vs `ModelUnavailableError`, `ProviderAuthError`, `EndpointUnreachableError`, `StructuredOutputError` |
| `fallback.py` | The model chain: `Chain`, `Candidate`, `chain_configs`, `classify` — which candidates exist, which share one allowance, and what each kind of refusal means for the next candidate |
| `geography.py` | The GIS boundary: `source_geography` — source values with field provenance; `source_locations` — the same values as `Location` mentions, only in the slots a mention can honestly hold, each left `pending_gis` with no canonical value. Names no feed field, resolves nothing |
| `spans.py` | Span arithmetic and replay: `SourceField`, `find_spans`, `locate`, `build_evidence`, `verify_evidence`, `revalidate` — the only sanctioned producer of `Evidence` |
| `contract.py` | The stable output: `Incident`, `Claim`, `Location`, `Entity`, `Relationship`, `SourceGeography`, `ContextFact`, `Provenance`, `Validation`, `Issue` and their enums |
| `checkpoint.py` | The resumable run store: `Checkpoint`, `Entry`, `fingerprint`. One durable line per record, carrying its full Incident, written atomically at each batch boundary, and a manifest `load` cross-checks before it trusts either file |
| `pipeline.py` | The orchestrator: `process_record`, `fetch_records`/`fetch_record`, `run_pipeline` (batching, resume, quota gating), `run_record`, `print_report`, `main` |
| `models/` | Shared pydantic vocabulary: `base.py` (`StrictModel`, `Confidence`), `enums.py`, `evidence.py` |
| `tests/` | The suite, plus `record_fixtures.py` and `builders.py` (the scripted and refusing provider doubles) |

## Current capabilities

- **Incident extraction** — title, description, event time and precision, locations, entities, relationships.
- **Classification from the contract's enums** — `incident_type`, `category`, `department`, `severity`, `priority`,
  `event_status`. The prompt's token lists are generated from those enums; no keyword or cue dictionary exists.
- **Evidence and provenance** — a quote is replayed against the untouched field text, cut to a span, and its id derived
  there. An invented quote, a quote from a field the record does not print, and a repeated quote with no offset chosen
  are refused, not repaired — and the refusal prints the offsets it could not choose between. Whole source trail on
  `provenance`.
- **One offset unit, for the whole layer** — `char_start`/`char_end` are Python `str` indices into one named field's
  untouched text: Unicode code points, 0-based, counted from that field's own first character. Never bytes, never UTF-16
  units, never an offset into the prompt. `spans.build_evidence_at` replays the cut and `Evidence` refuses a quote whose
  length does not equal `char_end - char_start`, so a span that does not reproduce the field's characters cannot be
  stored, and `field_text_hash` identifies the field by its whole UTF-8 digest rather than by standing in for an offset.
  A repeated phrase whose exact occurrence no answer resolved is `ambiguous_quote` and goes to review with the real
  offsets listed; an offset that is nearly right is refused as flatly as one that is far wrong, because guessing which
  copy was meant is the invention this layer exists to refuse. Tamil is proof rather than argument: the tests that
  measure the same quote as code points, as UTF-8 bytes, and across a character outside the BMP (which UTF-16 counts
  twice) name the one convention this layer keeps.
- **A headline is the record's own** — `title` is in `COPIED_FIELDS`: the record carries it whole, so an answer that
  rewrites, translates or quotes it without naming its field and span is refused and the record's own characters stand as
  the record's own copy (`SOURCE_METADATA`, `accepted`), with the answer's refusal still reported beside it and the
  incident still `review_required`. An abstention — no title offered at all — is the record's own headline too: the copy is
  held and published whenever the reading grounded something else, so a model that answered the description but skipped the
  title cannot blank a headline the record printed, and it stays a `SOURCE_METADATA` claim credited to the record rather
  than a claim that a model wrote it. Where the reading grounded nothing at all the copy is withheld, because the contract
  refuses an accepted claim inside an `unresolved` document and a record nobody read is honestly reported as unread rather
  than carried by its own title alone. Citing a span does not make an assembled headline the record's either: a `title`
  built from a span of `data.content` — a body sentence, or the navigation and duplicated date stamps a scraped page puts
  in front of its article — is refused as `unsupported_claim` even when the span replays, because the field the span sits
  in is not the field the record prints its title in. That
  furniture stays in `data.content` untouched: offsets and `field_text_hash` are computed against the stored field text,
  so cutting it would invalidate every span already grounded there. Cleaning a scrape is an `ingestion/` fix, not a
  reading-layer one.
- **Review states** — `accepted` / `review_required` / `unresolved`, with every finding carrying an `IssueCode`.
- **An event's clock, and which clock it is** — three sources, one field. A time the article's own words quote is
  evidence: `method = llm`, a cited span, `precision` as stated. A time equal to the record's own `event_time` is kept as
  `method = source_metadata` with no span and no evidence: the record's declared stamp, credited to the record rather
  than to the article, and on an incident named in `generation.warnings` because a news page's declared stamp is usually
  when the page was written. Its `precision` is what the answer stated, never inferred. A time equal to the instant the
  record was captured is refused outright and leaves `event_time` unresolved. Nothing borrows the retrieval time, and no
  accepted metadata stamp is ever reported as article-grounded.
- **A day is not a midnight** — on the value-shaped path, the *form* the record wrote its `event_time` in is carried out of
  `records.read_record` and into `event_time_precision`. A bare date (`"2026-10-03"`, or a `date` object) is filed as the
  contract's own day convention — the value is that day and `event_time_precision = day`, and the `00:00:00` a `datetime`
  has to carry is named in the claim's note as the day's stamp rather than as a moment anyone stated. A value that arrived
  with a clock time is copied exactly and left `precision = unknown`, because inferring a precision the source did not
  declare is the invention this layer refuses, and a record that gave no time is left without one. Forecast dates and
  market price days therefore no longer read as events at midnight UTC, and a forecast keeps its own day. The report line
  prints the same honesty: `event_time=2026-10-03` for a declared day, with no `T00:00:00` in it, and the full clock time
  for a stamp that arrived with one.
- **Context facts copy their field** — a fact's `value` must be the characters its named field really holds. A
  translated, rounded or glossed value is refused as an `unsupported_claim` with the field's own words printed beside it,
  the fact keeps its real span, and the record goes to review: an accepted fact never says something its field does not.
- **What the record said about itself stays in provenance, not in the claims** — the `CommonRecord`'s own `severity`,
  `status`, `event_time` and the form that time arrived in are carried on `provenance.declared_severity`,
  `declared_status`, `declared_event_time` and `declared_event_time_precision`, beside — never instead of — what the reader
  concluded, so a feed's band for itself ("Orange Alert", "UPDATED") can never be relabelled as an incident severity or an
  investigation status, and a consumer can still tell what the source printed from what this layer decided. No value is
  stored twice: the raw payload is not copied into the document, only these four declarations and the field paths below.
  `provenance.fields_without_value` then names every `data.*` path the record printed as `null` or `""` — measured over the
  October 2026 capture, all 61 stored IMD rows leave their humidities null and `forecast` empty, while none of its 2,373
  market rows prints an empty slot — so an empty field is an accounted-for absence rather than a dropped one, and no
  `""`/`"None"`/`"null"` is ever filed as a fact value.
  `provenance.input_hash` names the record bytes the reading was made against.
- **Record kinds** — `incident`, `forecast`, `context`, `unresolved`, so a sapota price line and a flood share one
  contract without either being a lie.
- **Structured processing with no model** — market lines and weather days are copied, not interpreted.
- **Bulk runs that can be stopped and resumed** — batches of `--batch-size` written to a `--state-dir`, a resumed run
  reads only what it has not read, and a checkpoint whose two files disagree is refused rather than trusted.
- **Quota-aware throughput** — a provider that refuses, on a busy window or on a spent allowance, ends the asking for
  that run; the records it never read are saved as deferred, not as failures, and the value-shaped records carry on
  regardless.
- **An ordered model chain, when one is configured** — a narrative record walks up to four verified candidates, one send
  each, and the kind of refusal decides whether the next one is asked at all. With no chain named the single
  `INTELLIGENCE_LLM_*` reader is the whole configuration, as it was before chains existed.
- **Attribution** — every semantic result states which candidate answered it and, when that was not the first, each turn
  and refusal that got there, kept in the checkpoint line beside the Incident.
- **Source geography, and the mentions it supports** — the record's own `location.*` values handed to GIS as source values
  with field-level provenance. On a value-shaped record the two slots a `Location` can hold faithfully — the district it
  named and any place it wrote as free text — also become `locations`, each citing the *same* field evidence as the
  `SourceGeography` entry behind it, at `resolution_state = pending_gis` with no canonical id, no coordinate and no
  confidence score: source metadata declares a place, it never verifies one. A state has no slot that survives the trip
  (`MentionType` has no `state` and `Location` has no state field) so it stays in `source_geography` alone, and a market
  name has no mention kind either — `MentionType` carries no `market` — so an `Uzhavar Santhai` is filed with kind
  `unknown` rather than guessed at as a town, a taluk or a district. A `location.district` value fills `Location.district`
  with itself and nothing else, so the five words for place never collapse into one. A narrative record's locations still
  come from its own prose with cited spans, exactly as before; the declared places beside it remain `source_geography`.
- **Tamil, English and mixed text** — a classification quote proves the event happened; it does not have to contain the
  English token.

Deliberately not implemented: OCR, chatbot, deduplication, cross-record correlation, trends, timelines, storage writes.

## Input

HTTP only. `pipeline.py` `GET`s `/records` and `/records/{record_id}` from `INTELLIGENCE_API_BASE_URL`
(default `http://127.0.0.1:8000`). It never opens a database connection, and nothing is written back unless the run is
invoked with `--post-incidents`. Records are read by declared field path against a structural protocol, so a renamed or
missing field fails loudly instead of surfacing as a silent `None`. `data` is source-specific, so both readers discover the
keys a record actually carries: `data.content` for an article, `data.warning`/`data.max_temp_c` for IMD,
`data.commodity`/`data.min_price` for markets.

**What the wire can no longer say about a day.** `ingestion/`'s normalizers hand `event_time` a `date` — an IMD forecast day
(`weather.py` builds it from `forecast_date`) and a market price day (`agriculture.py` from `price_date`) — but
`CommonRecord` stores it in a `DateTime` column and serves it as an `Optional[datetime]`, so what `GET /records` returns is
`"2026-10-09T00:00:00+00:00"`: an instant, with the fact that it was written as a bare day already gone. This module reads
the precision off the declared form when the record still carries one — a `date` object or an ISO string with no time part —
and **cannot recover it from a live weather or market row**; a stamp that arrives with a clock time is copied as it stands
and left `unknown` — including the real `WEATHER-MDU-03-Oct` shape `"2026-10-03T00:00:00"`, which is pinned that way in
`test_routing.py` because reading a midnight back into "it was surely a date" is inference, not a fact this layer holds —
and a record that gave no time is left without one. The smallest compatible change is upstream of this module's boundary:
put the declared precision on the wire — an optional `event_time_precision` beside `event_time` on `CommonRecord`, or have
the feed send the day as `"2026-10-09"`. `records.declared_precision` already maps either form to `TimePrecision.DAY`, so
no new code path is needed here, and `as_dict()` adds the key only for a bare date, so every clock-time record keeps the
`input_hash` it already had.

## Output

`contract.Incident`, `schema_version = "2.0"`, 24 fields: provenance, the claimed scalars, `record_kind`/`context_type`,
`locations`/`entities`/`relationships`, `source_geography`, `context_facts`, `evidence`, `claims`, `validation`,
`generation`. `to_storage_document()` serialises it; `Incident.model_validate_json` reads it back with its validators
intact. The contract refuses what a model might have invented: coordinates or a `canonical_location_id` without
`resolution_state = resolved`, accepted items citing nothing, dangling evidence references, duplicate ids, a
`SourceGeography` entry produced by a model rather than copied from a field, and `accepted` while issues are present.
`unresolved` and `unknown` are legal values — the absence of a finding, not a finding of absence.

## Routing

`router.route` decides from the record's own metadata and shape: a `source_type` of `agriculture`/`weather` whose `data`
is a non-empty mapping of single-line scalar values is `STRUCTURED`. A `data.content` body, a nested value, a multi-line
value, an empty `data` or a feed nobody has met is `SEMANTIC`. Uncertainty resolves toward the model, never toward a
table, because flattening prose into field copies would report a text as if it had been read. `structured.py` and
`geography.py` never import `llm.py`, so a structured record cannot reach a provider even by accident. A run builds a
provider once, and only when a narrative record still has to be read — a batch of price lines, and a batch whose prose an
earlier run already read, never authenticate.

## Current provider

| Config | What it does |
| --- | --- |
| `groq` | What this project deploys. Groq through the official `groq` SDK: one `chat.completions.create` per send, the extraction schema as `response_format` in strict JSON-schema mode, the credential handed to the SDK client and nowhere else, `finish_reason: length` refused as a spent completion budget rather than a half-written JSON to parse, and `base_url` reduced to `https://api.groq.com` whatever OpenAI-style path a stale `INTELLIGENCE_LLM_BASE_URL` still carries |
| `gemini` | Google's Gemini API through the Interactions endpoint over stdlib `urllib`: the credential in `x-goog-api-key`, `system_instruction`/`input` sent separately, the extraction schema sent as `response_format.schema`, and the response's own `status` read as its verdict — `incomplete` is truncation, never a half-written JSON to parse |
| `openai_compatible` | chat-completions over stdlib `urllib`, `json_object` hint. What an unset `INTELLIGENCE_LLM_PROVIDER` means |
| `vllm`, `ollama` | the same class with its own default base URL |

Configuration, not code: `INTELLIGENCE_LLM_PROVIDER`, `_MODEL`, `_BASE_URL`, `_API_KEY`, `_TIMEOUT_SECONDS`,
`_MAX_RETRIES`, `_MAX_WAIT_SECONDS`, `_TEMPERATURE`, `_MAX_TOKENS`, `_REASONING_EFFORT`, plus
`INTELLIGENCE_API_BASE_URL`. The names are already stable; adding a provider means adding one class and one registry
entry. **Bedrock and SageMaker are not implemented.** No key is hardcoded anywhere: `api_key` is `repr=False`, `summary()`
reports only `api_key_present`, a transport error carries the status and message, never the request headers, and the
chain's report names the variable holding a credential rather than its value.

Each vendor's own documented variable name is accepted as a fallback — `GEMINI_API_KEY`, `GROQ_API_KEY` — while
`INTELLIGENCE_LLM_API_KEY` is still asked first, so a deployment that already sets it keeps working unchanged, and a chain
entry's `api_key_env` outranks both. Asking the pipeline's own variable first for *every* provider is the one place a
credential can travel further than it was meant to: a key left in `INTELLIGENCE_LLM_API_KEY` for one vendor is handed to
whichever provider the run configures, so a chain deployment leaves that variable empty and names its own variable per
candidate. A provider whose sends go through an SDK rather than this layer's socket — `groq` — reports
`accepts_transport = False`, so a chain told to answer one of those through an injected transport is
refused as a configuration error instead of silently sending live.

`llm.py` reads the **repository root** `.env` (`load_dotenv(override=False)`), so an exported `INTELLIGENCE_LLM_*`
variable always wins; `intelligence/.env.example` lists placeholders only and `intelligence/.gitignore` keeps any local
`.env` out of the repository. An incomplete environment raises `ProviderNotConfigured` naming the missing variable.

### The model chain

`INTELLIGENCE_LLM_CHAIN_FILE` (or `--llm-chain FILE` for one run) points at a JSON list of candidates, asked in order —
at most `MAX_CANDIDATES` (4), because one record walks the whole chain and a long chain is a long wait rather than a
better answer. Unset means one candidate: the `INTELLIGENCE_LLM_*` configuration exactly as it stands. Each entry states a
provider and a model that are **actually deployed** — nothing here guesses a model id — and, where that provider needs a
credential, `api_key_env` naming the variable that holds it; a key written in the file is a configuration error, not a
convenience. Timeout, retries, wait budget, temperature, `max_tokens` and `reasoning_effort` are inherited from
`INTELLIGENCE_LLM_*` unless the entry states its own, so one budget governs the chain. A candidate naming another provider
is reached at that provider's own endpoint unless `base_url` says otherwise, so a fallback never re-asks the machine that
just refused. An entry this run cannot verify — no credential, no model, an unimplemented provider, a setting the
pipeline does not read — is dropped before anything is sent and named in the report as `Candidate dropped`; a file that
leaves nothing to ask, is not JSON, or holds no list stops the run with `ProviderNotConfigured` rather than falling back
to a guess.

What this project deploys is `intelligence/chain.groq.json`: **one** candidate — `openai/gpt-oss-120b` on Groq, stating
`provider: groq`, `base_url: https://api.groq.com` and `api_key_env: GROQ_API_KEY`, and inheriting every number from
`INTELLIGENCE_LLM_*` so one timeout and one wait budget governs the run. `INTELLIGENCE_LLM_CHAIN_FILE` names it;
`--llm-chain intelligence/chain.groq.json` names it for a single run. Paths open from the directory the run is started in.
One candidate is the decision, not an oversight: a fallback is a choice somebody makes and records, not something a
configuration does on its own, so a Groq refusal that closes that account leaves the record **deferred** for a later run
rather than quietly costing a send to a different vendor. A candidate whose provider needs a credential and whose
`api_key_env` holds nothing is dropped before anything is sent — and with nothing left to ask the run stops with
`ProviderNotConfigured` naming the file, the candidate and the variable that was empty, rather than inheriting
`INTELLIGENCE_LLM_API_KEY` and paying someone else for a refusal.

The chains this project no longer deploys are unchanged and still selectable. `intelligence/chain.gemini.json` is two
candidates on Google's Gemini API — `gemini-3.8-flash` then `gemini-3.5-flash-lite`, both `provider: gemini` at
`https://generativelanguage.googleapis.com` under `GEMINI_API_KEY` — and because they sit behind one endpoint and one
variable they are one scope, `https://generativelanguage.googleapis.com|GEMINI_API_KEY`, and one account's limits: a spent
allowance, a full window, a refused credential or a dead socket closes **both** on the first refusal, the second model is
skipped with its reason recorded in the checkpoint rather than sent to, and only a model id that endpoint will not serve,
or an answer neither model can make readable, gives the fallback its turn. `intelligence/chain.openrouter.json` is the
same shape on one `https://openrouter.ai|OPENROUTER_API_KEY` scope — `qwen/qwen3.8-flash` then `openai/gpt-oss-120b` — and
so is any single `INTELLIGENCE_LLM_*` reader naming `openai_compatible`, `groq`, `vllm` or `ollama`. Switching the deployed
chain changes what this project asks by default, not what the layer can ask; the classes it no longer deploys are still
tested.

What a refusal means for the next candidate is decided by `fallback.classify` and nowhere else, from the endpoint's own
words rather than its status code alone:

| Kind | What was refused | Whether the next candidate is asked | How the record ends |
| --- | --- | --- | --- |
| `quota` | the account's allowance is spent | only a candidate behind a **different** endpoint and credential; every one sharing that scope closes for the run | deferred |
| `rate` | this window is full | the same — a window belongs to the account, not to the model | deferred |
| `auth` | the credential was refused | only elsewhere; this model and its siblings behind that key are closed | deferred until the key is fixed |
| `model` | this endpoint will not serve this model | asked — the endpoint is fine, this model is not | read by whichever model serves it; failed if none do |
| `unreachable` | the connection failed or timed out | only elsewhere; that endpoint is closed for the run | deferred — a later run may find it up |
| `output` | it answered with nothing readable as the JSON object asked for | asked — nothing is repaired, the failure is kept | failed once every candidate had its turn |
| `shape` | the answer cannot be grounded, or the schema rejects it | asked | failed once every candidate had its turn |
| `transient` | a 5xx, a body that is not JSON, a send that was refused after its own bounded retries | asked | failed once every candidate had its turn |
| `record` | the record itself could not be read | never — another model would fail on it the same way; the chain re-raises | the orchestrator's own diagnosis stands |

Two rules keep this from becoming a retry storm: one send per candidate per record (a candidate's own retries are
bounded by `INTELLIGENCE_LLM_MAX_RETRIES` and `INTELLIGENCE_LLM_MAX_WAIT_SECONDS` as before), and a scope that refused on
its allowance, its window, its credential or a dead socket stays closed for the rest of the run — the refusal was
addressed to the account, so asking a second model behind it would only cost a second refusal. A record the chain could
not place because nothing was left to ask is `ChainExhaustedError` → **deferred**, exit **4**; a record every candidate
answered badly is `ChainFailedError` → a **failure** carrying each turn, and the run continues. Both messages keep the
turns in them, so the checkpoint line says who was skipped and why. A chain never touches a `STRUCTURED` record: value
rows are copied, and no candidate is asked for them.

## Running it

```bash
python -m intelligence.pipeline                    # safe default: five records, nothing written to disk
python -m intelligence.pipeline --record-id WEATHER-MDU-03-Oct
python -m intelligence.pipeline --all --json       # every record, full Incident documents

python -m intelligence.pipeline --all --state-dir runs/madurai            # resumable bulk run, 25 per batch
python -m intelligence.pipeline --all --state-dir runs/madurai --semantic-concurrency 4
python -m intelligence.pipeline --all --state-dir runs/madurai --no-resume # read everything again
python -m intelligence.pipeline --record-id NEWS-MDU-WLR-0001 --llm-chain intelligence/chain.groq.json  # one record, the deployed chain

# a small re-read of particular records, pinned by id, into a state directory nothing has read into yet.
# --limit 6 would take the first six records the backend returned, which is not the same claim.
for id in NEWS-MDU-fbb1b8b1bd809612 NEWS-MDU-3eda6936fb2742e5 NEWS-MDU-caf0c933c1a793e2 \
          NEWS-MDU-d7e026050541d904 NEWS-MDU-37de736e0fb1c6cc NEWS-MDU-cc93365b11a62ad1; do
  python -m intelligence.pipeline --record-id "$id" --llm-chain intelligence/chain.groq.json \
    --state-dir intelligence/runs/groq-batch-6-fresh
done
```

The report prints the backend URL, how many were read and took each reader, then one line per result — id, title, kind, the
permitted `context_type` under `context=` (`-` on an incident line, so a forecast's or a price line's five `unresolved` event
columns are never the whole of what it printed), type, category, department, severity, priority, status, event time, `facts=`
count, review state with its finding count and `path=structured`/`path=semantic` — grouped into accepted incidents, accepted
context and forecasts, review-required results, records deferred by a busy window or a spent allowance, records never sent
because the run had already stopped asking, failures, and the results a resume returned from the checkpoint without reading
them again. When a chain ran, it
also prints `Model chain`, `Chain scopes` (each endpoint with the variable naming its credential, and which of them
refused), `Read by`, `Read by a fallback` and a `Chain turns` block giving every refusal that cost a candidate its turn,
and each result line carries `read by=<provider>/<model> after <n> turn(s)`. `Read by` counts only the records this run
asked; readings it returned from the checkpoint are counted apart, under `Read by an earlier run`, so a resumed run
cannot report a copy as a send it made — and a result reused from a store written without a chain still says who produced
it, since that label comes off the saved line rather than out of this run's configuration. The counts close on
themselves: `fetched = selected + not selected`, `selected = reused + attempted + never sent`, and `attempted` is the sum
of the buckets the run decided, so nothing deferred or unread is ever reported as processed. Exit codes: **0** no record
failed (a contextual result is processed, not a failure), **1** a record could not be processed, **2** backend failure,
**3** no provider could be built *and* the batch asked for one, **4** the provider refused further reads and narrative
records were left deferred, **5** the checkpoint could not be trusted, **6** a record `--post-incidents` was told to send
came back rejected or unreadable (a `409` duplicate is not this failure). `--limit` is client-side because `GET /records`
has no paging, and it is positional: `records[:limit]` of whatever order the backend returned, default 5, so `--limit 6`
means the first six records in that order and promises nothing about which ids they are. A run that must see particular
records names them with `--record-id`, and `--all` clears the limit and refuses to be combined with `--limit`.

## Bulk runs and resume

`GET /records` answers the whole table at once, so batching is internal: a run fetches once, splits the selected records
into `--batch-size` groups, and writes progress at each batch boundary. A batch is the unit of recovery — stopping a run
mid-batch costs that batch's reads, never the batches already written.

With `--state-dir` the run owns two files, and nothing is written without it (`.gitignore` keeps `runs/` out of the repo):

| File | What it holds |
| --- | --- |
| `results.jsonl` | One line per record, rewritten whole so a record never appears twice: `record_id`, `state`, `mode`, `prompt_version`, `prompt_digest` (the prompt text and schema a semantic reading was answered against), `schema_version`, `input_hash`, the `provider`/`model` that produced it (semantic lines only), `attempts` (each candidate skipped or refused before the one that answered), `source_type`, `record_type`, `error`, the full `incident` document, `saved_at` |
| `checkpoint.json` | The run's shape, and the manifest the results are checked against: when it was written, contract version, backend URL, provider and model, the chain's candidates, record count, per-state counts, the results filename and its SHA-256 digest, superseded-entry count |

`state` is `completed`, `review_required`, `failed` or `deferred`; only the first two carry an Incident and are ever
reused. `results.jsonl` is the log, `checkpoint.json` its manifest: `load` compares the stated record count, the per-state
counts and the SHA-256 digest against the file beside it and refuses with a `CheckpointError` naming the field when they
disagree — a manifest with no results, one counting more records than the log holds, a digest that matches nothing, counts
that contradict their own total or the log, metadata that is not JSON or does not state what it must. A refused
checkpoint exits **5** before the backend is read, with both files left as they were. Only one disagreement is
recoverable, and only an interruption can produce it: the log ahead of its manifest, because the log is replaced first.
Such a run resumes on every line it holds and keeps saying which log it could not verify, because that note is the only
trace of what the run chose to trust.

Reuse is per record and per reading. A saved result is reused only when `record_id`, reader (`mode`), the reading's
version (`structured-3` / `stage-2.17`), `schema_version`, `input_hash` and — for a semantic reading — `prompt_digest` all
match. `input_hash` is the whole read record, text and declared place, so a reissued record whose wording or district
changed is read again rather than answered from the store. Only a record that wrote its `event_time` as a bare date
contributes `event_time_precision` to that hash: `as_dict()` adds the key when the form was declared and not otherwise, so
every clock-time stamp keeps the hash it had before this convention and nothing already read is forced to be read again
because a field appeared. `prompt_digest` is the same rule pointed at the prompt:
`PROMPT_DIGEST` in `intelligence.py` hashes the prompt text every narrative record is read with together with the schema
it is answered against, so rewording a rule changes the digest and what an older wording produced is read again — the
version label beside it no longer has to be moved for that to hold, which is exactly the gap that let a `stage-2.8`
reading be reused after the event-time rule changed. Two docstrings in `intelligence.py` are part of that digest rather
than commentary: Pydantic copies the `ExtractionDraft` and `ContextFactDraft` class docstrings into the schema the answer
is validated against as `description`, so deleting them as documentation silently rewrites what a provider is asked and
strands every stored semantic reading — `test_llm.py` pins the digest so that cannot happen quietly again, and
`test_prompt_budget.py` pins the shape the prompt prints from that same draft. A semantic entry written before digests
existed carries none and is read again
with that stated as the reason: this run cannot tell whether the wording still matches. A **semantic** result is also
reused only when this run could have produced it. On a chain, the entry's `provider`/`model` must be one of the
candidates this run asks — any of them may answer for a given record, so a reading by any of them is a reading this run
trusts. Without a chain, `pipeline.provider_identity` must name the same model, each side named by the configuration it
runs on rather than by the provider class — `vllm` and `ollama` are one class serving two endpoints — and a known
difference on either half re-reads the record. Structured results carry no provider, model or digest and are never
invalidated by one — a copy is a copy whoever runs it, and they key on `MAPPING_VERSION`. That is the only version this
pass moved: `SCHEMA_VERSION`, `PROMPT_VERSION` and `PROMPT_DIGEST` stayed where stage 2.17 left them and no key of
`as_dict()` changed, so every stored `input_hash` still matches its record. A value-shaped line written before the
declarations existed is therefore re-read by the deterministic copier, which asks no model and spends no quota, and a
narrative line is reused exactly as an earlier run produced it. Moving
`INTELLIGENCE_API_BASE_URL` invalidates nothing: a matching fingerprint proves the input was identical, and the report
says so instead of hiding it. Where either side of a comparison is unknown (a run with no model configured, an entry
written by such a run) nothing is blocked on it — a run that cannot say who read a result may not declare it stale. A
checkpoint from before the manifest cross-check states no results digest and is refused rather than half-trusted: use a
new `--state-dir`.

A re-read that ends refused or deferred cannot erase a finished reading. The completed Incident is the only copy of it,
so `Checkpoint.save` keeps the earlier line and reports the record under `Kept as an earlier run read them` — naming the
state it was kept as, the state this run's re-read ended, and the error — instead of writing a failure over work the run
could not repeat. It is then a stale result by the rules above, not a lost one.

A resumed run reports what it recovered rather than reporting nothing: alongside `Records attempted` the summary prints
`Records reused`, `Reused incidents`, `Reused contextual` and `Reused review-required`, lists the reused results in
their own block, and names every saved result it read again with the reason (`it was read by model X while this run is
configured for Y`, `it was read with prompt A while this run asks with B`, `it was read by X/Y, which is not one of the
candidates this run asks`). Reused results are never reprocessed, never re-saved, and never asked of the provider.

Refusal is separated from failure in `llm.py`, and classified from what the endpoint answered rather than from its status
code alone: an answer naming an exhausted allowance raises `QuotaExhaustedError`, a busy window raises
`RateLimitedError`, a model the endpoint will not serve raises `ModelUnavailableError`, a refused credential
`ProviderAuthError`, a connection that failed or timed out `EndpointUnreachableError`, and a `429` that explains neither
is retried once and then deferred as unclassified rather than guessed at either way. Where the answer states a wait, that
wait is honoured; where it states none, a busy window spreads
its budget over the sends it is allowed, capped per record by `INTELLIGENCE_LLM_MAX_WAIT_SECONDS` (default 60) — a wait
longer than the cap stops the sends instead of being half-waited. The scheduler reacts to the first refusal of either
kind: it stops asking that provider for the rest of the run, marks every narrative record it has not finished
`deferred` — the ones a refusal answered, and the ones it never sent, which decided nothing and are counted apart — keeps
every structured result and every already-read Incident, flushes, and exits **4**. It claims no
reset time, because nothing in the response establishes one. Without a chain it asks no other model either: a run stops
asking the provider it was configured with. With one, `fallback.classify` decides whether the refusal was about the
account, the endpoint, the model or the answer, and only the refusals that say "this model" or "this answer" cost another
candidate a turn. Deferring rather than failing is
what makes the next run take those records: a resume re-reads exactly the `deferred` and `failed` lines and reuses the
rest, so an interrupted run loses nothing it had already paid for. Concurrency is opt-in and capped at 8
(`--semantic-concurrency`, default 1: sequential), still one prompt per record, so no batch of unrelated articles ever
shares a completion. A refusal under concurrency defers the reads that had not started; requests already sent are not
recalled — they finish, and whatever they answered is saved. Under a chain, candidates are closed behind a lock, so two
records walking it at once cannot both spend a send on an endpoint that already refused.

## Boundaries

Intelligence reads `CommonRecord`s. It does not own ingestion or normalisation, does not write PostgreSQL or run the
backend, and does not resolve geography: no canonical id, no coordinate, no taluk/block/village, no district-code or
market-id lookup. `Location.resolution_state` is where GIS records the decision and `pending_gis` is this module's last
word. A feed's machine-readable geography stays a `context_fact` under the path the feed printed rather than being
relabelled, because picking it out would mean naming it. The `locations` a value-shaped record now carries are therefore a
hand-off, not a result: each says what the record declared, cites the field that declared it, and leaves
`canonical_location_id`, `latitude`, `longitude` and `confidence` null for GIS to fill — `pending_gis` on a place that came
from source metadata is a claim that a *name* was recorded, never that a boundary was resolved. Two schema gaps are left by
that rule rather than worked around: `MentionType` has no `market`, and `Location` has no state slot, so a market name keeps
kind `unknown` and a declared state stays in `source_geography`. Naming a `market` mention kind and a state slot is the
smallest contract change GIS may want, and it is Alagan's and Alex's call, not one this module makes alone. Nothing outside
`intelligence/` is changed to make this work, and no other team changes its contract to consume it.

### What this pass did not fix

- **One `event_status` field answers two questions.** The contract has a single status for an incident, so "the protest
  was held" and "the drain is still blocked" cannot both be stated. Nothing here splits them and nothing infers one from
  the other: prompt rule 6 says a protest, a strike, a court hearing, an inspection or a meeting is an event, not an
  ending, and where the answer cannot say which clock it is speaking of, `event_status` stays `unknown` and the record
  goes to review. Splitting the field would be a shared-schema change, which is out of this module's boundary. One
  stored live reading shows why the wording is not enough by itself: `NEWS-MDU-cc93365b11a62ad1` carries
  `event_status = resolved` while the quotes behind `incident_type` and `category` — both describing a protest — were
  refused for their offsets. The status was kept because the answer cited a span for it, and the layer cannot judge from
  the token that the span proves an event rather than an ending. What changed is that rule 4 says so, the digest changes
  so that record is read again, and the offline suite pins the wording being sent; none of that proves a live model
  obeys it, which is why a `resolved` on a protest-shaped record stays a thing to look at in the review queue.
- **A model's offsets are invented until they are replayed.** Measured across the six live Madurai readings, every
  `char_start` a model stated fell short of its quote's real position, each by a different number of characters — not by
  a constant factor, which is what a byte or UTF-16 miscount produces. So the deterministic layer cannot repair a span,
  only refuse it, and a phrase that appears more than once in a field needs an offset that is exactly one of its copies or
  the evidence is `ambiguous_quote` and reviewed. `stage-2.12` says the unit and the near-miss rule in the prompt; it
  does not make the numbers trustworthy, it makes wrong ones visible.
- **The accuracy of what a model chooses to quote is unchanged.** This pass tightened refusals and reporting. Where a
  reading is now correct that was wrong before, it is because the wrong answer is no longer accepted, not because a
  better answer was coaxed out.
- **The stored six live readings are history, not a result.** They were written as `stage-2.11` against prompt digest
  `c9c277914ac07499`; this layer now asks `00c1a6b51e8467a7`, and its deployed chain now names a Groq model rather than
  the Gemini pair that produced them, so a run over that state directory reads the narrative records again on two counts
  — the wording and the reader. They remain valid evidence of what the older wording and the older account produced, and of
  nothing else. The value-shaped half those runs used to reuse (`structured-1`, which neither a prompt change nor a
  provider change touched) has since been retired twice more — `structured-2` for the day/precision and
  declared-location work, `structured-3` for the declarations and the empty-field list this pass puts in provenance — so a
  run over those old state directories reprocesses them, which asks a model nothing and costs no quota, and its stored lines
  then carry the corrected semantics. Only the mapping version moved: `SCHEMA_VERSION`, `PROMPT_VERSION` and `PROMPT_DIGEST`
  are where `2.17` left them and no record's `input_hash` changed, so no narrative reading is retired and none is re-asked.
- **A flattened answer is refused, not prevented.** `NEWS-MDU-d7e026050541d904` and `NEWS-MDU-caf0c933c1a793e2` came back
  from the live reader with every field null and a `title.value` holding a serialization of the whole answer the model
  meant to write. Nothing in this layer assembles that string, and no prompt wording stops a model from producing it;
  what the layer does is refuse it, keep the record's own headline, and leave the reading in the review queue. A re-read
  may still arrive flattened. And the navigation-and-date furniture itself is written by `ingestion/` into
  `data.content` — out of this module's boundary — so it stays quotable as body text for spans other than the title.
- **A narrative record's own scalars are still not copied.** `COPIED_FIELDS` holds `title` alone. An article that also
  prints a `data.*` scalar the model never cited keeps that value only in `provenance.fields_without_value` (if empty) or
  not at all, because auto-copying every scalar into facts on the prose path would publish a field nobody read beside
  claims a model did read, and would change what a `review_required` narrative document asserts. Value-shaped records are
  where scalars live, and the router sends them there without a model call.
- **The round-trip reads the response, not the row.** `api_client.post_incident` re-validates what `POST /incidents`
  returns and refuses a body that changed the contract, and the offline tests pin that a stripped or rewritten
  `record_kind`, `context_type`, `context_facts`, `source_geography` or `provenance` is caught. The backend's POST response
  is the request object it echoed (`backend/app/api/incidents.py:50-69`), so what that check proves is the response path;
  the stored column is written verbatim from `to_storage_document()` at `:15-47` and read back at `:115-119`, and the one
  verification this layer cannot make from the response alone is a `GET /incidents/{incident_id}` after the create. That
  read-back is the platform's to add, or the thing to look at during the controlled integration run.

## Stage status

| Stage | Work | Status |
| --- | --- | --- |
| 1 | LLM foundation, `Incident` contract, evidence-first validation, provider abstraction | Complete |
| 2–2e | Real extraction, runnable orchestration, accuracy and semantic reasoning | Complete |
| 2f | Two readers: `router.py` + `structured.py`, no provider or quota for value-shaped records | Complete |
| 2g | Source-aware structured data and the GIS resolution boundary: `geography.py`, `Incident.source_geography` | Complete |
| 3 | Bulk processing: internal batching, checkpoint/resume, deferral on a busy window or a spent allowance | Complete |
| 3b | Configurable multi-model fallback: `fallback.py`, one send per candidate, scope-closed refusals, attribution per result, prompt-digest reuse | Complete |
| 3c | A deployed reader on one OpenRouter account: `intelligence/chain.openrouter.json` — `qwen/qwen3.8-flash` then `openai/gpt-oss-120b` under `OPENROUTER_API_KEY`, one account's limits honoured as one scope | Complete |
| 3d | The deployed reader on Google's Gemini API: `GeminiProvider` over the Interactions endpoint, `intelligence/chain.gemini.json` — `gemini-3.8-flash` then `gemini-3.5-flash-lite` under `GEMINI_API_KEY`, the extraction schema sent as `response_format.schema`, `status: incomplete` refused as truncation rather than parsed | Complete |
| 2.10 | The live-reading pass, prompt-side plus honest reporting: a repeated subject word grounds no classification and its refusal names the offsets, a context fact that translates its field is refused with that field's own words beside it, and a metadata `event_time` says whose clock it is | Complete |
| 2.12 | The stabilization pass, offline: the offset unit stated in the prompt and pinned by tests that rule out bytes and UTF-16, a near-miss offset refused rather than snapped to the nearest copy, a quote the article inflected differently refused rather than rewritten, an ungrounded `title` replaced by the record's own characters instead of blanking, the test session's socket and credential guards made order-independent, and the `--limit` and reuse-attribution wording corrected to what the code does | Complete (offline) |
| 2.13 | The scraped-page title pass, offline: traced the Tamil news record that came back `unresolved` to an answer whose only content was a flattened serialization of itself, refused a `title` assembled from a span of `data.content` — body sentence or the navigation and duplicated stamps a scrape leaves in front of the article — so the record's own headline always stands as the record's own copy, and proved the furniture cannot be cut without invalidating stored spans | Complete (offline) |
| 3e | The deployed reader moved to Groq `openai/gpt-oss-120b`: `GROQ_API_KEY` resolvable through the config layer and a chain entry, `intelligence/chain.groq.json` holding **one** candidate so a fallback stays a decision rather than a default, `intelligence/.env.example` repointed, and `chain.gemini.json` / `chain.openrouter.json` left selectable — no provider class rewritten, no dependency added, no second extraction path | Complete (offline) |
| 2.14 | The prompt compaction pass, offline: `prompt_text()`'s rules cut from 12,997 to 10,976 characters (the whole prompt 18,389 → 16,118, −12.4%) by removing duplicated framing while every grounded behaviour stayed instructed — 13 rules, the flattened-answer refusal, the district-is-only-a-district rule and the conflicting-evidence abstention named explicitly; `REQUEST_SCHEMA`, the two docstrings inside it, the answer shape, the taxonomy lists and every deterministic refusal are untouched | Complete (offline) |
| 3f | The write-back pass, offline: `--post-incidents` now posts the run's accepted Incidents from **both** collections, so a resumed run that reused its checkpoint writes its results back instead of saying it had none to send, while review-required, deferred and failed results stay unposted (accepted context joined the posting set at 2.17) and one incident id is sent at most once per run (`RunSummary.postable`) | Complete (offline) |
| 2.15 | Forecast date semantics and context geography, offline: `records.read_record` keeps the *form* `event_time` arrived in, so a record that wrote a bare day is filed at `event_time_precision = day` on the contract's own day convention instead of claiming an event at midnight UTC, a stamp with a clock time is copied unchanged and left `unknown`, and a missing date stays missing — with the report line
printing a declared day as that day rather than as a midnight; `geography.source_locations` turns a value-shaped record's declared district and free-text places into `locations` citing the same field evidence at `pending_gis`, with no canonical id, coordinate or confidence invented and a state or market name left un-guessed; `MAPPING_VERSION` `structured-1` → `structured-2` so older value-shaped checkpoint lines are re-read deterministically rather than reused with empty `locations` | Complete (offline) |
| 2.16 | The context payload's way to the dashboard, offline: proved that a forecast or price line keeps `record_kind`, `context_type`, every `context_fact`, `source_geography`, `evidence` and `provenance` through serialisation *and* through a checkpoint reload, that an entry written by an older mapping version is re-read deterministically rather than reused thin, and that at that stage none of it was posted to `POST /incidents` — the posting half is reversed by 2.17, the payload half still holds; `--post-incidents` now says **why** it had nothing to send when the records it read were context, and the README maps each dashboard column to the field path that already carries it | Complete (offline) |
| 2.17 | Accepted context writes back, and the prompt is cut again, offline: `POSTABLE_KINDS` lets `--post-incidents` post accepted `forecast` and `context` readings beside incidents under their own `record_kind` — nothing reclassified, `validation.review_required` still excluded and an `unresolved` kind never sent — with a `409` reported as `already exists (duplicate)` at exit `EXIT_OK` and a backend that echoes the body without the kind caught by `api_client`'s round-trip check as `EXIT_POST`; and `prompt_text()` compacted from 16,118 to 14,760 characters (−8.4%) — the repeated answer shape grouped (1,660 → 1,024) and some 660 characters of restatement out of the rules — with every taxonomy list, the offset unit, the verbatim-span rule and every deterministic refusal still sent, `PROMPT_VERSION`/`PROMPT_DIGEST` moved to `stage-2.17`/`00c1a6b51e8467a7` and pinned by a new `test_prompt_budget.py` | Complete (offline) |
| 2.18 | The data-integrity pass, offline: the record's own declarations kept out of the claim layer's way — `provenance.declared_severity`/`declared_status` and `declared_event_time` + `declared_event_time_precision` carry what the feed printed beside what the reader concluded, with an `orange_alert` band held as a declaration and never as a severity; every empty or null `data.*` path is named in `provenance.fields_without_value` instead of vanishing, and no `""`/`"None"` is filed as a fact; `provenance.input_hash` travels on the document; a headline the record printed is restored whenever the reading grounded anything else, and withheld when it grounded nothing, so the contract's own refusal of accepted claims in an `unresolved` document still holds; a cyclone warning and a disaster-worded headline are held as a forecast and a review rather than accepted as an incident, and a hypothetical tsunami label is refused as `invalid_value`; `MAPPING_VERSION` `structured-2` → `structured-3` so value-shaped checkpoint lines written before these fields are re-read deterministically for free — tested by storing a thin `structured-2` line and resuming with no provider; `SCHEMA_VERSION`, `PROMPT_VERSION` and `PROMPT_DIGEST` deliberately untouched; and the round-trip refusals pinned per contract-critical field | Complete (offline) |
| 4+ | Entity/location canonical resolution hand-off (GIS), evidence revalidation, dedup, trends, briefing, OCR, chatbot | Planned |

Bedrock and SageMaker remain unimplemented — one provider class and one registry entry each. The chain asks only
candidates a configuration verified: it never invents a model id, and never assumes that two models behind one endpoint
and one credential hold separate allowances.

`GeminiProvider` reaches the API over stdlib `urllib` rather than through `google-genai`: the SDK is not installed here,
the layer already needed a bounded-wait send policy and a status code it can classify, and adding a requirement to read
one documented endpoint buys nothing. Every request field is built in one place, so a field Google renames is a
correction in one function rather than a migration.

`GroqProvider` is the exception the opposite way: it was already built on the official `groq` SDK, so the migration to
`openai/gpt-oss-120b` added no dependency and no code path — its sends leave through `httpx` inside that client, which is
why it reports `accepts_transport = False` and why its offline tests inject a fake client instead of a transport. The one
number it can be configured with that its endpoint will not always accept is `INTELLIGENCE_LLM_REASONING_EFFORT`: `groq`
forwards whatever value is truthy, so `none` and `default`, which this layer offers for other endpoints, are risks on
Groq rather than reductions there, and the shipped deployment asks `low`.

## Integration contract

- Storable columns include `incident_id`, scalar fields, `event_time` **and the `event_time_precision` that says how to
  read it**, `provenance.*`, `validation.state`, and `generation.model` / `prompt_version` / `input_hash`. A consumer that
  shows a time should show `day` as a date: `2026-10-03T00:00:00` with `precision = day` is the forecast's *day*, and
  rendering it as a clock reading is the one place the honesty of this field is lost.
- **`provenance` now carries what the record said about itself**, additively and with `schema_version` still `"2.0"`:
  `declared_severity`, `declared_status`, `declared_event_time`, `declared_event_time_precision`, `input_hash`, and
  `fields_without_value` — the `data.*` paths the feed printed empty or null. A consumer that reads a stored document can
  therefore tell a blank in the source from a refusal by the reader, and neither from an invented value. An older document
  without these keys still validates (they default to `null`/`[]`), so nothing stored before this pass is unreadable; what
  it lacks is the declarations, and a value-shaped record carrying `prompt_version = structured-1` or `structured-2` in a
  checkpoint is re-read rather than reused.
- **A declared band is not a severity.** Where a feed writes `severity` or `status` about itself — `"Orange Alert"`,
  `"UPDATED"` — that value is preserved in provenance and the contract's `severity`/`event_status` stay
  `unresolved`/`unknown` unless evidence supported them. A consumer that wants the feed's band should read
  `provenance.declared_severity` rather than expect it to have been mapped into an enum it is not in.
- **The GIS hand-off has a shape now.** A `location` with `resolution_state = pending_gis` and `method = source_metadata`
  is a place the record declared, not one anyone verified: the canonical id, coordinates and boundary are GIS's to set, and
  this module leaves them null on purpose. Resolving them must not rewrite `text`, the cited evidence or the `review` state
  the source value arrived with.
- `to_storage_document()` serialises the whole incident; `Incident.model_validate_json` reads it back with its
  validators intact.
- **A context record has a full payload and the same endpoint as an incident.** `record_kind`, `context_type`,
  `context_facts`, `source_geography`, `locations`, `evidence` and `provenance` all survive the document, a checkpoint
  reload and the `POST /incidents` body a `--post-incidents` run sends for an accepted forecast or price line; what is
  still missing is a read side that filters on them. See *Context for the dashboard* for each column's field path and the
  one integration the platform owner has to make.
- **View Source** depends on `provenance.source_url` and `raw_reference` being preserved, and accepted claims' evidence
  ids resolving to spans that replay against stored field text.
- `review_required` needs human confirmation. `unresolved` and `unknown` are non-authoritative: absence of a finding,
  not a finding of absence.
- The API client communicates over HTTP: it reads CommonRecords and can optionally post accepted Incidents, forecasts and
  context records. Intelligence never writes to PostgreSQL or connects to the database itself.

## Context for the dashboard

A forecast and a market price leave the structured reader as the same `Incident` document, with `record_kind` saying
`forecast` or `context` and every value the record carries sitting in `context_facts` under the field path it came from. The
event-shaped fields stay `unresolved`/`unknown` because nothing in the record evidenced an event, and that is exactly what
`--post-incidents` sends: the kind the record evidenced, with the event fields left unfilled rather than guessed. Nothing
has to be invented to fill the two views:

| The card wants | Where it already is |
| --- | --- |
| Forecast day | `event_time` read together with `event_time_precision` — `day` means print the date, `unknown` means the wire had already flattened it (see *What the wire can no longer say about a day*) |
| Weather text or warning | `context_facts` on `data.forecast`, `data.warning` |
| Max / min temperature | `context_facts` on `data.max_temp_c`, `data.min_temp_c` |
| Commodity and market | `context_facts` on `data.commodity`, `data.market` |
| Prices, unit, quantity | `context_facts` on `data.min_price`, `data.max_price`, `data.unit`, `data.quantity` |
| Reporting date | `event_time`, plus `provenance.declared_event_time` and `provenance.declared_event_time_precision` for what the feed itself wrote, and `provenance.retrieved_at` for when it was fetched |
| District, state, market place | `source_geography` on `location.district`, `location.state`, `location.raw_text`, and `locations` for the same values as mentions |
| A band the feed printed about itself | `provenance.declared_severity`, `provenance.declared_status` — held as declarations, so a market row's `"UPDATED"` or an IMD `"Orange Alert"` is shown without becoming an incident `severity` or an investigation status |
| A field the feed left blank | `provenance.fields_without_value` — the `data.*` paths, so a card can print "not reported" for the field the source actually left empty instead of for one this layer refused to guess |
| Source and provenance | `provenance.source_url`, `raw_reference`, `record_type`, `source_type`, `input_hash`, and one `evidence[]` row per field |
| That it is not an incident | `record_kind` and `context_type` — never a keyword match over a title |

A fact row is verbatim what the reader copied out of the record's own field, and its evidence id resolves inside the same
document:

```json
{"fact_id": "fact-7", "field": "data.min_price", "value": "46.0", "method": "source_metadata",
 "confidence": null, "review": "accepted", "evidence_ids": ["ev-AGRI-MDU-ANNA_NAGAR-TOMATO-2026-10-03-f97f5434ccd9"],
 "notes": "the record holds this value in this field"}
```

```json
{"evidence_id": "ev-AGRI-MDU-ANNA_NAGAR-TOMATO-2026-10-03-f97f5434ccd9", "record_id": "AGRI-MDU-ANNA_NAGAR-TOMATO-2026-10-03",
 "source_id": "tn_agri_marketing", "source_type": "agriculture", "field": "data.min_price", "quote": null,
 "field_text_hash": "0729c13ebd725201c1445a00c825237d305ff650cd72f50e45259bd942a75ef4", "span_validation": "not_applicable",
 "modality": "structured_data", "source_url": "https://agrimark.tn.gov.in/home/getPrice_dir/2026-10-03/21/125",
 "retrieved_at": "2026-10-03T14:03:29.086508Z"}
```

So a card renders `value` without re-reading the record and can still cite where it came from. `confidence` is `null`
because a copied field is not a guess, and `locations[].latitude`, `longitude` and `canonical_location_id` are `null`
because they are GIS's to set.

**What already leaves the process.** `--state-dir` writes `results.jsonl`, one line per record, and each line's `incident`
member *is* this storage document — contextual records included, and a reused line is revalidated through the contract, so
what a resume hands back is byte-for-byte the payload above (`test_batch.py` pins the facts, geography, evidence and
provenance of both readings). `--json` prints the same documents. That is a durable, contract-versioned export, but it is a
run artifact, not an API.

**What the platform still has to build.** Nothing outside `intelligence/` reads the fields that separate a forecast from
an incident: `record_kind`, `context_type`, `context_facts` and `source_geography` appear **zero** times across
`backend/app`, `backend/tests` and `frontend/src` (measured). The write gap closed at stage 2.17 — `--post-incidents`
sends an accepted forecast or price line to `POST /incidents` under the `record_kind` the record evidenced — so the two
pages have something stored to read. The read gap is the platform's, and it is four named integrations, none of which this
pass was permitted to make:

- `backend/app/api/incidents.py:73-111` — `GET /incidents` filters on `incident_type, category, severity, priority,
  event_status, district, start_date, end_date` and nothing else: no `record_kind`, no `context_type`, no limit or offset,
  so a client that wants weather alerts or price lines must page the whole table. `:111` and `:119` return
  `Incident.model_validate(record.document)` and `:15-47` stores that document verbatim, so the payload the README
  documents is what the columns already hold — the filter, not the schema, is the missing piece.
- `backend/app/api/incidents.py:50-69` — the POST handler returns the request object it was handed, so a client that
  compares its response against what it sent proves the response path, not the stored row. A `GET /incidents/{id}`
  after the create is the one check that closes it; `backend/tests/test_incidents_api.py:131-146` asserts
  `created.json() == payload` and `payload in listed.json()`, and no test in `backend/tests` names `record_kind` or
  `context_facts`, so nothing on the backend side pins that a context record survives storage.
- `frontend/src/utils/incidents.js:93-104` — the weather view is a keyword regex (`weather|rain|rainfall|flood|cyclone|
  storm|drought|heatwave|landslide|lightning`) over `incident_type`, `category`, `title`, `description`,
  `provenance.source_type` and every `evidence[].quote`; `:85-91` picks agriculture by `category`,
  `provenance.source_type` and `provenance.record_type`. Replace both with `record_kind` (`forecast`, `context`) and
  `context_type` (`weather_forecast`, `market_price`), which is what `Dashboard.jsx:225` (weather), `:245` (agriculture)
  and `:405-407` (their counts) call, and a tsunami question about Madurai stops appearing as a weather alert while a
  genuine flood report stops being filtered out of it.
- `frontend/src/pages/Dashboard.jsx:91-123` — the overview KPIs are `incidents.length` and per-category totals over every
  stored document, so an accepted forecast is counted in "Total incidents" the moment it is posted. The fix is a filter on
  `record_kind === 'incident'`, not a change to how this layer classifies: `record_kind` says what the record evidenced,
  and dressing a price line as an incident to make the table look right is the invention the whole module refuses.
- `frontend/src/components/IncidentTable.jsx:88-93` renders `category` ("Unresolved"), `incident_type` ("Not specified"),
  `severity`, `priority` ("—") and `event_status` ("Unknown") as columns, so a forecast or price row arrives with those
  cells empty-looking while its actual content — `context_facts[].value` under `data.min_price`, `data.warning` — is not
  in any column. `:96-98` already prints `event_time` with `formatPrecision(event_time_precision)`, which is the day
  convention surviving to the UI. `frontend/src/components/EvidenceList.jsx:7-11` prints "No source excerpt was provided"
  for the structured evidence rows this layer writes with `quote: null` on purpose, while `:15` and `:19` do show their
  `field` and `span_validation` — so a card that wants the number needs `context_facts`, not a quote.

None of that is done here, and none of it can be: no file outside `intelligence/` was touched by this pass, so the
dashboard renders what it rendered before it. What changed is that the documents those views would filter on now carry
their own declarations, their empty-field list and their kind, and that a `--post-incidents` run writes them.

## Tests

```bash
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

543 tests, all offline. `test_pipeline.py` 86, `test_batch.py` 80, `test_spans.py` 54, `test_llm.py` 45,
`test_fallback.py` 42, `test_orchestration.py` 36, `test_routing.py` 32, `test_grounding_regression.py` 32,
`test_gemini.py` 30, `test_api_client.py` 20, `test_contract.py` 19, `test_groq_deployment.py` 15,
`test_geography.py` 14, `test_evidence.py` 12, `test_offline_guard.py` 12, `test_title_fidelity.py` 8,
`test_prompt_budget.py` 6. No test touches
a real provider or a live backend: prose runs use the `Scripted` double from `tests/builders.py` plus the counting,
refusing and out-of-quota doubles in `tests/test_batch.py`, value runs use `NeverCalled`, which fails the test the moment
anything asks a model a question, and every checkpoint file is written under pytest's `tmp_path`.

The data-integrity tests assert structure, not sentences. A parametrized sweep in `test_routing.py` runs five value-shaped
fixtures — a tomato line, the chilli/`Anaiyur Uzhavar Santhai` line, an IMD forecast, a cloud-and-rain warning and a
cyclone warning — through the deterministic reader with no provider and requires that every non-empty `data` value come
back as the string the record holds, that every empty or null one be named in `provenance.fields_without_value`, and that
provenance equal the record's own ids, urls, `retrieved_at` and declarations field for field; the same file requires that
a cyclone warning whose prose names a disaster stays `forecast`/`weather_forecast` with `incident_type` and `severity`
`unresolved`, `priority` null and `event_status` unknown. `test_pipeline.py` holds the narrative shapes: an answer that
omits the clock leaves `event_time` unresolved while provenance keeps the declared stamp and its precision, a record that
printed no time is given none by its provenance, an `Orange Alert` band the feed printed about itself is preserved as a
declaration and refused as a severity, a meeting report baited with flood words stays a review rather than a case, and a
residents' association asking whether a tsunami could reach Madurai is refused as `invalid_value` for its label.
`test_api_client.py` writes out the POST body a price line produces and then, parametrized per field, strips or rewrites
`record_kind`, `context_type`, `context_facts`, `source_geography` and `provenance` in the response to show the round-trip
refuses each — including a backend that fills in a `declared_severity` for a row whose feed printed none. `test_batch.py`
stores a value-shaped checkpoint line written before these fields existed, reloads it, and shows it is re-read for free
with its kind, context type and every fact field and value unchanged while provenance gains the declarations the thin line
never carried; the line it writes then names the current mapping, and a narrative entry's `input_hash` is untouched by any
of it, so no prose record is re-asked and no quota is spent.

Three layers make that more than a claim, and `test_offline_guard.py` is the proof of each.

- **The socket is the boundary, not a proxy.** A session fixture replaces name resolution, socket creation, `connect`,
  `connect_ex` and datagram sends with one refusal, so a live Gemini, Groq, OpenRouter, OpenAI or backend request cannot
  happen by accident whatever transport it arrived on. The guard tests send through `urllib`, through `httpx`, through
  `fetch_records`, and through a `GeminiProvider` handed a credential on purpose, and each raises that refusal — with
  `HTTP_PROXY`/`HTTPS_PROXY` set as well, because a dead proxy is what used to be the check and is not one: it stops a
  send only when the code chooses to honour it, and this layer's own socket path does not.
- **The deployment is out of the environment.** A session fixture strips every `INTELLIGENCE_*` and `*_API_KEY` variable
  before the first test and repoints `llm.DOTENV_PATH` at a file that does not exist, so no test can load the
  repository's live credentials however it constructs its configuration — an earlier pass of this suite did exactly that
  and made billed calls. A test that supplies a provider is never handed a chain the environment names: `run_pipeline`
  asks the caller's reader and nothing else.
- **A test's own environment does not outlive it.** Removing the variables once is not enough, because reading an env
  file is `load_dotenv`, which writes its keys into the live `os.environ`, and a `delenv` recorded before that read has
  nothing to restore for a key that was absent then: a provider test pointing `DOTENV_PATH` at a deployment file of its
  own therefore left that endpoint, model and credential in the process for every test after it. A function-scoped
  fixture now takes the environment back after each test, and two guard tests show both halves — that a file really does
  write through, and that the write did not survive. This was found by running the whole directory rather than one file:
  the credential check passed alone and failed in company, so it is now order-independent, verified by running the suite
  forwards and with every module in reverse.

`test_pipeline.py` pins the refusals as plainly as the extractions: a quote the record does not contain, a field it does
not carry, a band its taxonomy has no name for, an event time that is only the instant the page was captured, and an
answer that calls a record both a happening and a context type.

The stage-2.10 tests read one Tamil body several ways. With both classification fields cited on the word that body
repeats three times, each is refused with the offsets named while the act, the band and the place the same body states
stay accepted; give one of them an offset and that one alone is grounded; cite the clause that names the authority and
both land accepted with no issue at all. A context fact quoting the Tamil `12 டேங்கர் லாரிகள்` while answering
`12 tankers` is refused with the field's own words printed in the finding and keeps its real span, the same fact in the
field's own characters is accepted, and the three event-time routes — a date the body states, the stamp the record
carries, nothing at all — each say which of them it was, so no record reads as though the article had dated what only
its metadata dated.

`test_grounding_regression.py` replays the six Madurai news records offline, and nothing about them is a live claim. The
bodies are sanitized under the real record ids as fixture keys, written around the phrases that actually caused the
failures so the fixture proves its own shape: `நடந்தது` three times, `மதுரை` three times,
`மத்திய தொல்லியல் துறை` three times, `என்.ஜி.ஓ., காலனி`, `ராஜேந்திர பிரசாத் தெரு`,
`திருப்பரங்குன்றத்தில்` and `ஆர்ப்பாட்டத்தில் ஈடுபட்டனர்` twice each. The scripted answers reproduce the shapes the
endpoint really answered with, the invented offsets among them. Per record: the answer that failed live fails the same way
here and the incident lands `review_required`; what the refusal struck out is not still claimed as a fact; an
`ambiguous_quote` finding names the offsets it could not choose between; an offset that is nearly right is refused with
the message saying it is none of them; the same answer carrying one of the true offsets grounds, and that record comes
back with no issues at all and no review state. Beside the replays sit the measurements that rule a convention in or out —
code points rather than UTF-8 bytes, one character rather than the two UTF-16 units a character outside the BMP would
count as — the quote the article had inflected differently (`ஒன்றிய அதிகாரிகள்` for its `அதிகாரிகளிடம்`)
refused and never rewritten to fit, a field the answer named but the record does not carry saying which field to name
instead, and the three title routes: a rewritten headline dropped for the record's own characters with the refusal still
reported, a headline quoted without naming its field the same way, a headline never answered left as the abstention it
was. The prompt text itself is pinned for the wording that changed — the offset unit stated as Unicode characters of the
field named, a near-miss offset refused as flatly as a far one, a protest or an inspection never `resolved`, and
`--limit`'s help saying which records a limited run takes. The bookkeeping is tested in the same file: a span-free answer
surviving the Gemini transport it would have been sent to, a resumed run splitting who it asked from who it only
returned, a fresh `--state-dir` reading the same record again, a limited run keeping the front of the order the backend
returned with the accounting equation closing on it, and a `review_required` reading keeping that state when a later run
hands it back rather than being reported as a clean one.

`test_batch.py` holds the checkpoint's failure modes as well as its happy path: fourteen ways a manifest can fail to
account for the results beside it, a manifest with no results, results with no manifest, a crash landing between the two
replacements (and the recovery run that keeps naming the log it could not verify), a run stopped before the backend was
read, a model or endpoint change that re-reads a prose record while a price line stays reused, a backend move matched on
the record instead of the URL, and a resumed mixed dataset whose summary names what it reused with buckets that close
against the records it selected — no record counted twice, none dropped.

`test_fallback.py` holds the chain's policy: each way a chain file can fail to be a configuration (not JSON, no list,
nothing verifiable left, a fifth candidate, a provider or model that is not there, a credential variable holding
nothing), and, through a real `OpenAICompatibleProvider` answered by a transport that replies per model — the first
candidate answering so nothing else was asked, a `model_not_found` giving the next one its turn, a busy window closing
every model behind the same endpoint and credential while a candidate on another account still answers, a spent allowance
ending the asking across records rather than costing one refusal each, a refused credential closing its own models and not
someone else's, an unreachable endpoint deferring the record, prose or a non-JSON body giving the next model a turn and
never being repaired, a completion cut off at its token budget naming that as the cause, a record that cannot be read
never re-asked of another model. Plus the checkpoint side: attribution saved beside the Incident, a deferred record asked
again on resume, a finished reading surviving a run that could not re-read it, a chain's own readings reused while an
off-chain one is re-read, a reworded prompt and a digest-less entry both re-read rather than quietly reused, a chain
sending nothing for a price line, and the scopes note naming endpoints and the credential's *variable* with no key value
in it.

Eight of them read the deployed OpenRouter file itself rather than a `tmp_path` copy, with a base configuration naming another service,
another endpoint and another credential. Both candidates come back as `openai_compatible` at
`https://openrouter.ai/api/v1` under `OPENROUTER_API_KEY`, the primary first, neither one built yet, the inherited numbers
arriving while none of the inherited identity does — and with no OpenRouter key anywhere they hold no credential at all
rather than that other service's. The file's own JSON is read for the same guarantee: two candidates, four keys each, no
`api_key` among them. Then the account, answered per model id through a real provider: `insufficient_quota` costs one send
and closes the fallback without asking it, and the next record of the same run is deferred with nothing sent for it either;
a `rate_limit_exceeded` behaves the same while it lasts; a `model_not_found` gives the fallback its turn and closes only
the model nobody serves, so the record after it goes straight to the survivor; a `401` reports the account and never the
credential; and a run through `run_pipeline` stores and prints the fallback's `openai_compatible/openai/gpt-oss-120b`, a
model id with a slash in it, as the reader.

`test_gemini.py` holds the Gemini path, and nothing in it can send: the request is checked as the payload a transport
would have carried — the `/v1beta` endpoint reached once rather than doubled, the credential in `x-goog-api-key`, the
prompt's rules kept in `system_instruction` apart from the record, one budget carried as `max_output_tokens`, and a
thinking level only where the API has one (`none` becomes `minimal`, `default` sends nothing). The schema conversion is
checked against the subset that endpoint documents: `$defs` inlined, a nullable field collapsed to `["string","null"]`,
`propertyOrdering` and `additionalProperties` gone, and the answer's own field named `title` surviving a converter that
must not mistake property keys for schema keywords. What comes back is read as the endpoint means it: `output_text`, or
an answer that exists only in `steps[]`, or no text at all, or `status: incomplete` reported as truncation with the
endpoint's own thought and output counts and the half-written JSON never parsed. Each refusal shape is classified the way
the chain needs — `RESOURCE_EXHAUSTED` naming `RATE_LIMIT_EXCEEDED` a busy window honoured at the endpoint's own
`retryDelay`, the same status naming `alloc_quota_exceeded` a spent allowance that stops the sending immediately,
`UNAUTHENTICATED` and `PERMISSION_DENIED` auth, `NOT_FOUND` model, a 5xx retried inside the wait budget and a 400 not
retried, a wait the endpoint asks for that is longer than the whole budget stopping at one send, and a 429 that named
neither allowance nor window deferred as a limit rather than falsely reported as spent quota. Three read the shipped
`intelligence/chain.gemini.json` as configuration (both candidates `gemini` at `https://generativelanguage.googleapis.com`
under `GEMINI_API_KEY` in one scope, inheriting its numbers and none of its identity) and stop the run before the first
send when no key is held anywhere; six more answer that chain per model id. The accounting is checked through
`run_pipeline`: the store naming `gemini` and the primary model, both candidates in its allow-set, the entry saved with
the model that actually answered, and a `record_kind` that arrives as an object still refused by name with nothing coerced.

`test_groq_deployment.py` holds the migration, and because a Groq send leaves through an SDK it mocks that SDK — every test
replaces `groq.Groq` with a factory that records how the client was built and what it was sent, so nothing in the file can
reach an endpoint even though the socket guard would stop it anyway. The configuration is read from the environment the way
a deployment will set it: `GROQ_API_KEY` resolves, `INTELLIGENCE_LLM_API_KEY` still outranks it, and a Gemini credential
alone leaves a Groq reader with **no** credential and a refusal naming both variables before any client exists; an
OpenAI-style `INTELLIGENCE_LLM_BASE_URL` is reduced to `https://api.groq.com`, with `max_retries=0` so this layer's own
wait budget is the only one in force. The shipped `intelligence/chain.groq.json` is read as configuration — one candidate,
four keys, `GROQ_API_KEY` named and no key held, inheriting the numbers of a base configuration that names Gemini and none
of its identity, and refusing the run outright when its variable is empty rather than borrowing another vendor's. That a
fallback cannot be accidental is proved the direct way: `PROVIDERS["gemini"]` is replaced with a class that fails the test
the moment it is constructed, and a whole `run_pipeline` over the deployed chain still completes. The wire is checked
against what the layer promised — the system message starts with `prompt_text()`, the rules count is exactly 1..13, and
`response_format` carries the strict schema with the same top-level `description` and the same properties as
`REQUEST_SCHEMA`, so the compaction moved the wording and not the contract. What comes back is grounded like any other
reader's answer: exact Tamil spans replay at their offsets, the record's own headline stands, a body sentence sent as the
headline is refused and left `review_required`, a flattened serialization of the whole answer names no field and supports
nothing, and a `severity` token the taxonomy does not carry resolves to nothing rather than to the nearest one. Two more
hold the boundaries the migration could have crossed: a value-shaped record on the same chain never builds a client at all,
and a reading Groq stored is not reused by a run whose candidates no longer include Groq — while the same store, asked with
Groq in its allow-set, reuses it.

Stage 2.15 is pinned by ten new tests, all of them asking a question of a record and never of a model. Four in
`test_routing.py` hold the clock: a forecast that wrote `2026-10-03` is filed at that day with `precision = day` and the
note that says the midnight is the day's stamp, the same day handed over as a `date` object reads identically, a stamp
carrying `14:32:05+05:30` is copied character for character and left `unknown` because nothing here inferred it, and a
record that stated no time is left with `event_time = None` rather than a fabricated one — with `record_kind`,
`context_type` and every `context_fact` still intact in each case. Four in `test_geography.py` hold the places: a market
name written as free text keeps `location_type = unknown` instead of being guessed at, a declared place arrives at GIS with
`method = source_metadata` and `review = accepted` (what the record said) but `resolution_state = pending_gis` and no
canonical id, coordinate, normalized name or confidence, each `loc-N` cites the *same* evidence id as the `geo-N` entry
behind it so no field is quoted twice, and `source_locations` on a state value alone returns `[]`. One in `test_batch.py`
runs a day-only forecast and a price line through a checkpoint, reloads the store, and finds both reused with nothing asked
again — the reused Incident still `precision = day`, its two locations still `pending_gis`, and the stored JSONL line
carrying `"event_time_precision": "day"` and both place names, with the storage round-trip equal to the object it came from.
One in `test_orchestration.py` keeps the console honest: the result line for that forecast prints
`event_time=2026-10-03` with no `T00:00:00` anywhere in it, while a record that stated `14:32:05` prints the clock time it
stated.

Stage 2.16 adds three, and none of them asks a model anything. Two are in `test_batch.py`, following a record through the
store and back: the first reloads a day-only forecast and a price line and asks for the *whole* document rather than just
its kind — every `context_fact` field and value, every `source_geography` path, every `evidence_id`, the full `provenance`,
and the stored JSONL line's own `record_kind`, facts, geography, evidence and `source_url`; the second re-reads with
`MAPPING_VERSION` held back at `structured-1` and shows the record comes round again instead of being reused thin, with the
same facts, the same values and the same deterministic evidence ids, and names the retired version as the reason. The third
watched that forecast go through `main` twice and proved no POST left the process; stage 2.17 reversed that expectation, and
the test holding the reversal is `test_a_reused_forecast_is_posted_and_the_backend_copy_reports_a_duplicate`.

Stage 2.17 is seven posting tests, six prompt tests and one moved digest. The posting half runs `main` with
`--post-incidents` over a mocked transport with `NeverCalled` behind it, so nothing in it can ask a model: an accepted IMD
forecast and an accepted agriculture price line both reach `POST /incidents` with their `record_kind`, `context_type`,
every `context_fact` value, their `source_geography`, `locations`, evidence rows and `provenance.source_url` intact and
their event-shaped fields still `unresolved`/`unknown`; a forecast a human flagged and an `unresolved` reading never reach
the transport at all; a forecast reused from a checkpoint posts and a `409` is reported as
`already exists (duplicate)` while the run still exits `EXIT_OK`, whether the backend answers the conflict with a bare
status or with a `{"detail": ...}` page; and a backend that echoes the body back without the `record_kind` it was sent
fails the post with `EXIT_POST` naming the contract it changed, rather than storing a forecast as an incident. The
selection is `POSTABLE_KINDS` and one `review_required` check, so what a run may write back stays one property. The prompt
half cut `prompt_text()` from 16,118 to 14,760 characters: the answer shape from 1,660 to 1,024 by printing the nine
fields that share one grounded signature as one line and the two token fields as one warning, and some 660 characters out
of the rules where a rule restated another rule's instruction or previewed one it then made. Nothing structural moved —
`REQUEST_SCHEMA` and its docstrings, the 206 taxonomy tokens the schema does not carry as enums, the offset unit, the
verbatim-span and abstention rules are all still sent, which `test_prompt_budget.py` holds to a ceiling and a floor, and
all 64 phrases the suite pins survived the cut. `PROMPT_VERSION` and `PROMPT_DIGEST`
moved to `stage-2.17` and `00c1a6b51e8467a7`, which retires the two semantic readings a `stage-2.14` run stored: the next
live pass re-asks those two records rather than reusing an answer the old wording produced.

Stage 2.18 is twenty-seven tests and no new question asked of a model — ten in `test_routing.py`, five in `test_pipeline.py`,
eleven in `test_api_client.py`, one in `test_batch.py`. They hold retention rather than wording: a sweep over five
value-shaped fixtures requires every non-empty `data` value to come back as the field's own characters and every empty or
null one to be named in `provenance.fields_without_value`, with provenance required to *equal* the record's own ids, urls,
`retrieved_at` and declarations; the chilli line from `Anaiyur Uzhavar Santhai` keeps all eight of its values, its market
id, its district code, its title and its source url; a forecast keeps both humidities and the day it was written for; a
cyclone warning stays a forecast with `priority` null and `event_status` unknown; and an `Orange Alert` the feed printed
about itself arrives as a declaration, not a severity. The narrative half shows that an answer which omits a clock leaves
the declared stamp in provenance and the field unresolved, that flood words in a meeting report and a question about a
tsunami reach review with an `invalid_value` refusal instead of a case, and that a headline the record printed survives a
reading that grounded everything else about it. The API half writes out the body a price line sends and then, field by
field, shows a backend that strips or rewrites `record_kind`, `context_type`, `context_facts`, `source_geography` or a
declaration is refused rather than stored — and the checkpoint half proves the one version this pass moved retires only
value-shaped lines, which cost nothing to re-read. The capture those figures come from has grown since the notes above:
2,527 normalized records now, 2,373 agriculture, 93 news, 61 weather, and every one of those 61 weather rows leaves its
humidities null and its `forecast` empty, which is the shape `fields_without_value` exists to account for.

Offline over the October 2026 Madurai capture (`ingestion/data/normalized`, read straight off those files with no model and
no backend — filtered to `Mode.STRUCTURED` before anything was called, and every call made against `NeverCalled`): 2,171
records — 2,031 agriculture, 85 news, 55 weather — route 2,086 `STRUCTURED` / 85 `SEMANTIC`; the structured half processes in
1.9 s, all 2,086 come back `accepted` with 16,468 `context_facts`, 6,258 `source_geography` entries and 4,172 `locations`,
and no incident carries a canonical id or a coordinate. Through the Stage 3 scheduler at that same volume (the capture's own
records, a state directory in the temporary filesystem, no model configured, no provider built): cold **31.0 s** at
`--batch-size 25` (**4.3 s** in one batch, since every batch boundary rewrites the whole `results.jsonl`), resume **4.5 s**
with 2,045 of the 2,086 reused — the 41 read again are records whose `record_id` the capture itself reuses across files with
different values, and the store keeps one line per id, so their fingerprints cannot all match it. 30.4 MiB of stored
Incidents. (Those `--batch-size 25` numbers are the current capture; the older 1,372-record figures in the stage notes above
were measured before the feed grew, and `run_pipeline` only reuses from a store the caller loaded — `Checkpoint(dir)` alone
reads nothing, which is why the CLI calls `store.load()` in `main`.)
