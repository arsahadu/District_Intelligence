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
documents.py  --document FILE                 — a local scan or PDF read into that same shape
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
| `documents.py` | The document reader: Tesseract TSV or a PDF's own text layer → page fields, word and page confidence, uncertain spans, and the `document` block that carries them to `context.py`. It recognises text and nothing else — no second extraction path, no verdict |
| `structured.py` | The value reader: copies each field into a `context_fact`, keeps the day a record stated as a day (`event_time_precision`), hands its declared geography to `source_locations`, files the record as `context`/`forecast`. No provider import, no prompt, no quota |
| `intelligence.py` | The semantic engine: request building, draft parsing, grounding, assembly |
| `llm.py` | `LLMProvider`, `LLMConfig`, `LLMRequest`/`LLMResponse`, `GeminiProvider`, `GroqProvider`, `OpenAICompatibleProvider`, `build_provider`, and the refusals read off what the endpoint answered: `QuotaExhaustedError` (allowance spent) vs `RateLimitedError` (window busy) vs `ModelUnavailableError`, `ProviderAuthError`, `EndpointUnreachableError`, `StructuredOutputError` |
| `fallback.py` | The model chain: `Chain`, `Candidate`, `chain_configs`, `classify` — which candidates exist, which share one allowance, and what each kind of refusal means for the next candidate |
| `geography.py` | The GIS boundary: `source_geography` — source values with field provenance; `source_locations` — the same values as `Location` mentions, only in the slots a mention can honestly hold, each left `pending_gis` with no canonical value. Names no feed field, resolves nothing |
| `spans.py` | Span arithmetic and replay: `SourceField`, `find_spans`, `locate`, `build_evidence`, `verify_evidence`, `revalidate` — the only sanctioned producer of `Evidence` |
| `contract.py` | The stable output: `Incident`, `Claim`, `Location`, `Entity`, `Relationship`, `SourceGeography`, `ContextFact`, `Provenance`, `Validation`, `Issue` and their enums |
| `checkpoint.py` | The resumable run store: `Checkpoint`, `Entry`, `fingerprint`. One durable line per record, carrying its full Incident, written atomically at each batch boundary, and a manifest `load` cross-checks before it trusts either file |
| `pipeline.py` | The orchestrator: `process_record`, `fetch_records`/`fetch_record`, `run_pipeline` (batching, resume, quota gating), `run_record`, `print_report`, `main` |
| `linking.py` | The first post-step (`--link-events`): candidate blocking on signals and date, one comparison per pair, evidence-cited `EventLink`/`EventGroup`, and `event_units` — the distinct happenings the counts below are built from |
| `recurrence.py` | The second (`--recurring-issues`): `Filters`, eligible incidents, place∧problem candidate groups, one grouping request each, arithmetic counts, `IssueFamily`/`IssueStore` |
| `trends.py` | The third (`--emerging-trends`): equal back-to-back windows over the same event units, per-window coverage, the four dimensions, the candidate rise, `Trend`/`TrendStore` |
| `copilot.py` | The fourth (`--ask`): one request reads a question into a search, the search runs with no provider, a second request words the answer — `QuestionIntent`/`QuestionScript`/`StatementKind`, `Corpus`, `search`, `Answer`, `AskReport`/`AnswerStore` |
| `briefing.py` | The fifth (`--briefing`): the period is measured here and only then phrased — `measure`/`Measured`, `available_rows`/`status_rows`, `brief_lines`, `brief_request`, `carried_actions`, `BriefItemRef`/`BriefLine`/`BriefAction`, `Briefing`, `BriefReport`/`BriefStore`, `briefing_document`/`print_briefings` |
| `models/` | Shared pydantic vocabulary: `base.py` (`StrictModel`, `Confidence`), `enums.py`, `evidence.py` |
| `tests/` | The suite, plus `record_fixtures.py` and `builders.py` (the scripted and refusing provider doubles) |

## Current capabilities

- **Incident extraction** — title, description, event time and precision, locations, entities, relationships.
- **Classification from the contract's enums** — `incident_type`, `category`, `department`, `severity`, `priority`,
  `event_status`. The prompt's token lists are generated from those enums; no keyword or cue dictionary exists.
- **Evidence and provenance** — a quote is replayed against the untouched field text, cut to a span, and its id derived
  there. A quote the named field does not hold is decided by the field that really carries it. Verbatim in exactly one
  other field the record prints, it is anchored in that field, byte-identical and replayable, with the mis-citation named
  in `generation.warnings`. Verbatim nowhere, but bracketed by its own word-aligned runs — a leading and a trailing run
  of eight characters or more, each taken from the citation itself — a field whose value is
  a taxonomy token (`incident_type`, `category`, `department`, `severity`, `priority`, `event_status`) is anchored on one
  of the stretches of the record those runs bracket. Every head-and-tail pair that fits is a candidate, and the one whose
  two runs together hold the most of the citation's own characters is anchored: where one sentence of the article shares
  only the citation's tail and another shares that tail plus a clause of its body, the evidence is the second, because it
  is the sentence the answer came nearest to quoting. The evidence is the record's own sentence, so what the answer rewrote
  between the runs is dropped rather than copied, and the recovery is disclosed in `generation.warnings` and in the
  claim's `notes`. It is refused instead of stretched when either run is shorter than eight characters or the citation's
  own, when a run prints more than eight times in its field, when the covered text is more than twice the citation, when
  the covered stretch is not unique, when the two runs together are longer than the citation, or when no candidate beats
  another — two stretches that bracket the citation with equal weight are refused as `ambiguous_quote` with both offsets
  named, since picking one would be this layer's choice rather than the answer's — and the bracket is never offered to
  `title`, `description` or a list item, because a rewritten sentence of prose is not evidence however neatly its ends
  match: `description` paraphrased is still `unsupported_claim`.
  Verbatim in no field and unbracketed, it is refused as `unsupported_claim`, so a value nothing in the record states
  never becomes evidence; verbatim only in a field that repeats it, with no offset to
  choose a copy, it is refused as `ambiguous_quote` rather than guessed into one field. A quote from a field the record
  does not name at all is still `unknown_field`, and the refusal prints the offsets it could not choose between. Whole
  source trail on `provenance`.
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
  than to the article, and on an incident named in `generation.warnings`. **Except on a news record:**
  `ingestion/app/normalizers/news.py:26` writes `published_at` into `event_time`, so that stamp is a publication time;
  offered as the event's time it leaves `event_time` unresolved (claim `unresolved`, no review demotion, a warning saying
  why) and survives only as `provenance.declared_event_time` (`PUBLICATION_STAMPED_SOURCES` in `intelligence.py`). It is
  the page's clock even when the answer pasted a real span beside it: a stamp equal to the record's own `event_time`
  down to the second is refused whether or not its quote grounded, because the article's words cannot make the page's
  timestamp a moment something happened — while a same-day time the article states, which is not that stamp, is still
  accepted. Its
  `precision` is what the answer stated, never inferred. A time equal to the instant the
  record was captured is refused outright and leaves `event_time` unresolved. Nothing borrows the retrieval time, and no
  accepted metadata stamp is ever reported as article-grounded.
- **A field the record never answered stays absent** — `priority` is the one scalar the contract leaves `null` rather than
  giving an absence token, so a priority claim the record cannot support is not filed as an unresolved claim with a
  review-triggering finding beside it: it is omitted, with the reason in `generation.warnings`, and the classifications
  the reading did ground stand as `accepted`. That only covers a claim refused for want of evidence — a priority outside
  the taxonomy still records `invalid_value` and still puts the record in review, and a priority whose span is doubted by
  the OCR reader still carries its evidence and its `review_required`.
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

Cross-record event linking is opt-in (`--link-events`, see *Linking reports of one event*), so is reading local
documents (`--document`, see *Documents and OCR*), so is counting recurring issues over a period
(`--recurring-issues`, see *Recurring issues*), and so is measuring whether those events are becoming more frequent
(`--emerging-trends`, see *Emerging trends*), so is answering a question about them in Tamil, English or mixed
(`--ask`, see *Collector Copilot*), and so is assembling the period into one district briefing a Collector reads without
asking (`--briefing`, see *District Briefing*). Deliberately not implemented: a chat service (no session, no endpoint, no
UI — one `--ask` answers one question in one run and keeps no conversation), a mailed or scheduled briefing (this stage
ships the document an email, a scheduler or a dashboard reads, and sends nothing itself), timelines, notification services,
storage writes.

## Input

HTTP only, apart from `--document`, which reads a local file into the same record shape (see *Documents and OCR*).
`pipeline.py` `GET`s `/records` and `/records/{record_id}` from `INTELLIGENCE_API_BASE_URL`
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
`unresolved` and `unknown` are legal values — the absence of a finding, not a finding of absence. What each field can tell the Collector's interface, and what this layer still cannot produce for it, is tabulated in *Integration contract*.

## Routing

`router.route` decides from the record's own metadata and shape: a `source_type` of `agriculture`/`weather` whose `data`
is a non-empty mapping of single-line scalar values is `STRUCTURED`. A `data.content` body, a nested value, a multi-line
value, an empty `data`, a `document`'s page fields or a feed nobody has met is `SEMANTIC`. Uncertainty resolves toward the model, never toward a
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
python -m intelligence.pipeline --all --state-dir runs/madurai --recurring-issues --since 2026-08-01 --category water
python -m intelligence.pipeline --all --state-dir runs/madurai --emerging-trends --recent-days 14 --min-rise 3
python -m intelligence.pipeline --all --state-dir runs/madurai --link-events --ask "Which incidents need follow-up?"
python -m intelligence.pipeline --all --state-dir runs/madurai --link-events --recurring-issues --emerging-trends \
  --briefing --brief-days 7
python -m intelligence.pipeline --all --state-dir runs/madurai --briefing --brief-language tamil \
  --since 2026-09-01 --until 2026-09-30 --category water

# a small re-read of particular records, pinned by id, into a state directory nothing has read into yet.
# --limit 6 would take the first six records the backend returned, which is not the same claim.
for id in NEWS-MDU-fbb1b8b1bd809612 NEWS-MDU-3eda6936fb2742e5 NEWS-MDU-caf0c933c1a793e2 \
          NEWS-MDU-d7e026050541d904 NEWS-MDU-37de736e0fb1c6cc NEWS-MDU-cc93365b11a62ad1; do
  python -m intelligence.pipeline --record-id "$id" --llm-chain intelligence/chain.groq.json \
    --state-dir intelligence/runs/groq-batch-6-fresh
done
```

The report prints the backend URL, how many were read and took each reader, then one line per result — id, title, kind, the
permitted `context_type` under `context=` (`-` on an incident line), then the six fields that each answer a question about a
happening — `type`, `category`, `department`, `severity`, `priority`, `status`. On a forecast's or a price line those six print
as `n/a`, because the contract itself refuses a value in them for a record that states no happening: `n/a` is the display,
while `unresolved`, `unknown` and `null` stay exactly what the serialized document carries, so an accepted contextual record
is never printed as six unresolved failures. `event_time` is not among them — a forecast does name the day it speaks about, and
a day stated at day precision prints as that day rather than the clock time it never named. A record carrying `context_facts`
prints its `facts=` count and then `values=`, one `field=value` pair per fact with the `data.` prefix dropped and a long value
clipped, so the commodity, both prices, the market, the temperatures and a warning no reader was asked to classify are all
inspectable on the line. `places=` follows: the place names the account itself evidenced, or, when it evidenced none, the
honest `places=none in the account (declared: Madurai;Tamil Nadu)` — which names the feed's own geography as what it is instead
of padding the line with a district the record never placed an event in. Review state with its finding count and
`path=structured`/`path=semantic` close the line. Results are grouped into accepted incidents, accepted context and forecasts,
review-required results, records deferred by a busy window or a spent allowance, records never sent because the run had already
stopped asking, failures, and the results a resume returned from the checkpoint without reading them again. When a chain ran, it
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
came back rejected or unreadable (a `409` duplicate is not this failure), **7** a `--document` could not be read. `--limit` is client-side because `GET /records`
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
version (`structured-3` / `stage-2.29`), `schema_version`, `input_hash` and — for a semantic reading — `prompt_digest` all
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

## Linking reports of one event

`--link-events` (with `--state-dir` to keep the decisions) asks whether two accepted incidents from different records
describe the same underlying event. It never merges, edits or deletes an Incident: each decision is a separate,
reversible `EventLink`, and an `EventGroup` is derived from the links (`linking.py`, prompt `link-1`).

| Step | What happens |
| --- | --- |
| Eligible | Accepted `incident` documents read or reused in this run; context and review-required readings are excluded |
| Candidates | Two records sharing at least one signal (incident type, category, a named village/place, a person) and published within 7 days of each other; at most 5 per incident, best-ranked first. Sharing only the district or state is not a signal, and neither is a place whose role is `reporting_origin`, `mentioned_only`, `institution_name` or `actor_affiliation` |
| Conflicts | The pipeline states them before the model is asked: dated event times on different days, different known incident types, disjoint resolved place ids |
| Comparison | One provider call per pair through the existing chain: both reports' quotes printed with their evidence ids, plus the shared signals and conflicts; the answer is `same_event`, `separate_event` or `undetermined` with confidence and cited ids |
| Decision | Accepted only at confidence ≥ 0.8 with no findings; `same_event` also needs a quote cited from each report and no unexplained conflict. Anything else — an invented id, a guess, a bad confidence, a conflict — is kept as `review_required` with the reason |
| Groups | Accepted `same_event` links form events (union-find). A group that also holds an accepted `separate_event` link is put in review rather than merged |

Each side of a link keeps `incident_id`, `record_id`, `source_id`, `source_url`, `published_at` and the cited
quotes, so every relationship replays against the original reports. The state directory gains `links.jsonl` (one
`EventLink` per pair, rewritten whole, so a pair never appears twice) and `events.json` (`schema_version`,
`prompt_version`, `prompt_digest` and the `EventGroup`s). Since 2.24 every link and every group carries the Incident
contract's `schema_version`, so a consumer can tell which incident documents the ids inside them name. A decision is
reused only when the text shown to the model, `LINK_PROMPT_DIGEST` and that `schema_version` are unchanged; a changed
reading, or a comparison decided against another incident contract, is asked again. A damaged `links.jsonl` exits **5**
and is not overwritten. A quota or exhausted chain stops comparing, leaves the rest as `not compared`, and exits **4**;
the report then prints `Distinct events: at most N`, because undecided pairs are not counted as merges.

Limits: a follow-up published more than 7 days later, or one sharing no signal, is never compared; only the incidents of
the current run are linked, so `--limit` narrows the pool; an event id changes when its membership changes; reuse is keyed
on the shown text, `LINK_PROMPT_DIGEST` and the incident `schema_version`, not on the model. All linking tests use scripted model answers — no live accuracy is
claimed.

## Documents and OCR

`--document PATH` (repeatable) reads a local scanned notice, image or PDF into the **same** semantic path as an article:
`documents.py` turns it into a `CommonRecord`-shaped dict (`source_type = document`, `record_id = DOC-` + the first 16 hex
of its SHA-256), `route()` sends it to `SEMANTIC`, and extraction, grounding, review and posting are the ordinary ones.
There is no second extraction path and nothing is read from or written to `GET /records`.

```bash
python -m intelligence.pipeline --document notice.png --state-dir intelligence/runs/docs
python -m intelligence.pipeline --document report.pdf --document-language en --source-url https://example.test/report.pdf
```

| Input | Engine | What is kept |
| --- | --- | --- |
| Image (`png`, `jpg`, `jpeg`, `tif`, `tiff`, `bmp`, `webp`) | Tesseract, TSV output, `-l tam+eng` / `tam` / `eng` | Text per page as `data.page_001`…, every word's confidence and box, the page mean |
| PDF | `pdftotext -enc UTF-8` (its own text layer) | Text per page; a page with no layer is empty and named in `fields_without_value` |

- **Source preserved.** `raw_reference` is the resolved file path, `retrieved_at` its modification time, `source_url` what
  `--source-url` gave (one document only); the `document` block on the record holds file name, SHA-256, media type,
  engine and version, languages, the two floors and each page's confidence, quality and uncertain words — not the text.
  Changed bytes or a different engine give a different `input_hash`, so checkpoint reuse never hands back a stale reading.
- **Recognised is not verified.** Evidence on a page carries `modality = scanned_document` (OCR) or `pdf` (text layer) and a
  note saying which engine read which page of which file and that it was not checked against the original. The model is
  told to quote page text exactly as printed, misreadings included.
- **Uncertainty goes to review.** A word below confidence **60** is uncertain; a page whose mean is below **70**, or a
  `--document-language ta` text layer holding no Tamil character (a legacy font encoding), is low quality. Any claim whose
  span touches an uncertain word, or lies on a low-quality page, keeps its evidence but is `review_required` with
  `uncertain_recognition` naming the words and their scores — so an uncertain name, date or place is never placed into the
  Incident's fields, and `--post-incidents` does not send it. An unreadable or low-quality page is also a generation warning.
- **Refused, not guessed.** A missing language pack (`tam.traineddata`) is an error rather than a read with another
  script's model; so are an unsupported suffix, an empty file, malformed engine output, an image with no recognised word
  and a PDF with no text layer at all. The run exits **7** before any model is asked.

Engines are found through `INTELLIGENCE_TESSERACT_CMD` / `INTELLIGENCE_PDFTOTEXT_CMD`, then `PATH`, then (Tesseract) its
default Windows folder. Limits on this machine: Tesseract 5.5.3 has only `eng`/`osd`, so Tamil images are refused until
`tam.traineddata` is installed; there is no rasteriser, so a scanned PDF must be exported to images first. The document-only
prompt line is not part of `PROMPT_DIGEST` (news requests are byte-identical). All tests use a scripted engine and scripted
model answers — no OCR or extraction accuracy is claimed.

## Recurring issues

`--recurring-issues` answers the question one Incident document cannot: is this the same problem keeps happening here?
It is a post-step over the incidents a run already read, in the shape linking is (`recurrence.py`, prompt `issue-1`). It
changes no Incident, merges nothing by hand, and needs no endpoint, table or migration: every answer is an `IssueFamily`
written beside the checkpoint.

```bash
python -m intelligence.pipeline --all --state-dir runs/madurai --recurring-issues
python -m intelligence.pipeline --all --state-dir runs/madurai --recurring-issues \
  --since 2026-08-01 --until 2026-10-31 --category water --min-severity moderate --place Karungalakudi
python -m intelligence.pipeline --all --state-dir runs/madurai --recurring-issues --json
```

| Step | What happens |
| --- | --- |
| Eligible | Accepted `incident` documents this run read or reused; review-required readings are left out and counted as left out, context and forecast rows never enter a count |
| Filters | Applied to incidents **before** events are formed: `--since`/`--until` against the event's own day, `--category`, `--incident-type`, `--department`, `--min-severity` (an `unresolved` severity is at least nothing), `--place` matched against every place name the record printed at any level. A filter given without `--recurring-issues`, or an `--until` earlier than its `--since`, is refused at argument parsing before the backend is read |
| Events | Distinct happenings, not rows: accepted `EventLink`s from `--state-dir` (or from `--link-events` in the same run) are union-found, so four reports of one flood are one event. The grouping never triggers linking; with no decisions saved every incident is its own event and the report says so in its limitations rather than guessing a merge |
| Date | One event's day is the earliest date its incidents carry, with `when_basis` naming whose date it is (`event_time`, the record's own declaration, or `retrieved_at`). An event with no date at all stays out of the period and is reported as an event that carries no date |
| Candidates | Deterministic blocking, not a keyword dictionary: two events are candidates only when they share a place key **and** share a known problem (incident type or category). A flood and a road accident in one village are never put in front of the model as one group, while two water reports worded differently are |
| Grouping | One request per candidate group through the existing chain: each event's own quotes with their evidence ids, its day and basis, its places, the statuses its reports recorded, and the shared signals. The model returns labels, descriptions, which events are one problem, cited ids, a department and one suggested action — it is asked for meaning, never for a count |
| Computed | Distinct event/incident/record counts, first and last day and the span, the per-month distribution, the areas each event named and the precision the family can claim, the change between the two halves of a period of at least **61** days, recorded responses and open follow-ups — all arithmetic over the records, and a family whose counts do not equal its own id lists is refused by the model validator |
| Safeguards | Accepted only at confidence ≥ **0.8**, with cited quotes from at least two of its own events, ids that were actually shown, a department the contract has, and no difference the model itself listed. Anything else is filed `review_required` with the findings beside it. A family of one distinct event is refused outright, so a single report cannot be an issue that recurs |
| Stored | `issues.jsonl` (one `IssueFamily` per line, each bucket rewritten whole) and `recurring.json` (`schema_version`, `prompt_version`, `prompt_digest`, the filters used, the counts, the limitations, the families) in `--state-dir` |

A grouping is reused only when the events shown to it, `ISSUE_PROMPT_DIGEST` and the incident `schema_version` are all
unchanged; a changed reading or a different incident contract is asked again. A damaged `issues.jsonl` exits **5** and is
not overwritten, a chain that stops answering leaves the remaining groups `not grouped` and exits **4**, and a group whose
answer was unusable is a failure that exits **1** — the run still reports what it did answer.

**Recorded work is not suggested work.** `recorded[]` and `followups[]` are copies of the `event_status` a report itself
claimed, each with its quote, record id, source url and timestamp: `action_taken` and `resolved` are recorded responses,
`reported`, `ongoing` and `under_investigation` are open follow-ups, and `unknown` is neither. A `suggested[]` action
carries `recorded = false`, `method = llm`, the department, the event and evidence ids it cites, its own confidence and
review state, and the console prints it as `(suggested, not recorded)`. Nothing here can say work was assigned, done or
acknowledged.

Limits on this stage: one request per candidate group, and at most **8** events in it with **10** quotes per event, so a
problem with more events than that is filed as a smaller issue and counted as truncated; areas are names at the level the
source named them and the family's precision is its least precisely known event; a recorded action is only as good as the
status sentence one report printed. All 55 tests use scripted grouping answers and hand-built link decisions — no live
grouping accuracy is claimed.

## Emerging trends

`--emerging-trends` answers the question an article count cannot: is this happening more often here than it was? It is a
post-step over the incidents a run already read, in the shape recurring issues are (`trends.py`, prompt `trend-1`). It
changes no Incident and needs no endpoint, dashboard, notification service, table or migration: every window, count,
mean, ratio and rate is arithmetic over the records, and every answer is a `Trend` written beside the checkpoint.

```bash
python -m intelligence.pipeline --all --state-dir runs/madurai --emerging-trends
python -m intelligence.pipeline --all --state-dir runs/madurai --emerging-trends \
  --since 2026-08-01 --until 2026-10-31 --category water --min-severity moderate --place Karungalakudi
python -m intelligence.pipeline --all --state-dir runs/madurai --emerging-trends \
  --recent-days 14 --baseline-blocks 4 --min-events 4 --min-rise 3 --coverage-drift 2
python -m intelligence.pipeline --all --state-dir runs/madurai --emerging-trends --json
```

| Step | What happens |
| --- | --- |
| Windows | One recent window of `--recent-days` (**30**) days ending at `--until` or at the latest dated event this run counted, and `--baseline-blocks` (**3**) baseline windows of *the same length* sitting back to back before it. Fewer than **2** of them fit in the history the records reach, so nothing is measured and the report says how far back the records go and how many whole windows that leaves. Unequal windows are refused by the validator, because a 30-day count against a 45-day average is not a comparison. A filter without `--emerging-trends`, a setting without it, an `--until` before its `--since`, a `--recent-days` outside 1–365, a `--baseline-blocks` under 2, a `--min-events` under 2, a `--min-rise` or `--coverage-drift` under 1.0 are all refused at argument parsing |
| Events | Distinct happenings, not rows, exactly as in [Linking reports of one event](#linking-reports-of-one-event): accepted `EventLink`s from `--state-dir` (or from `--link-events` in the same run) are union-found, so four reports of one flood raise one event in one window and not four. The comparison never triggers linking. With no decisions saved every incident is its own event, and that is carried as a finding on every trend the run prints, not written off |
| Date | One event's day is the earliest date its incidents carry and `when_basis` says whose date it is (`event_time`, the record's own declaration, `retrieved_at`). An undated event sits in no window and is counted as an event with no date; a dated one outside the series is counted as outside the windows rather than as absent |
| Patterns | Four dimensions, each a value the records themselves carry — `category`, `incident_type`, `place` (the `level:name` a record printed, never a resolved boundary), and `issue_family` (an accepted `IssueFamily` from `--recurring-issues` in the same run or from `issues.jsonl` in `--state-dir`). Grouping is dict-key arithmetic over those values, so a road accident never joins a flood's count and no keyword dictionary decides anything |
| Candidates | A pattern is a candidate increase when its recent window holds at least `--min-events` (**3**) distinct events **and** its count is above the baseline average. Every candidate becomes a `Trend` whether or not it clears the thresholds: a 1.2× rise is reported, with the threshold it missed named in its findings. Candidates rank by ratio, then by events |
| Coverage | The two numbers a bare count can fake, both computed: these events **per record the run read** in the window against the baseline's rate (`--min-rise`, **2.0**, must still be reached after the correction), and **reports per event** in the recent window against the baseline's (`--coverage-drift`, **1.5**), plus the publishers that appear only in the recent window. A window holding no record of any kind, or a baseline of zero events, says the run read nothing here or that this is a new appearance — never that a rise was measured |
| Interpretation | One request per candidate through the existing chain, at most **6** per run: the printed windows with their event/incident/record/publisher counts and the records held in each, the arithmetic summary, then up to **8** of the recent events with their own quotes, ids, days, places and statuses. The model returns a title, one sentence on the pattern, whether the shown events are one problem, what else could fit the same numbers, which numbered events it relied on, and a confidence — the answer schema has no count field, so nothing it writes is used as a number or can move one the pipeline measured |
| Status | `alert_eligible` only when every rule passes: deduplicated events with none still undecided, at least `--min-events` events, both rises at or over `--min-rise`, no unread window, a non-zero baseline, at least max(**2**, `--min-events`) of its own events carrying a quote from their own record, and a reading at ≥ **0.8** that names ≥ **2** of the events it was shown and did not answer that they are different problems. Anything else is `potential`, and the reason sits beside it on the line — a trend cannot list a finding and still call itself eligible, and `Trend` refuses that outright along with a rewritten count, a mismatched id list or an id derived from another window, so a hand-edited `trends.jsonl` cannot arrive as an alert |
| Stored | `trends.jsonl` (one `Trend` per line, rewritten whole) and `trends.json` (`schema_version`, `prompt_version`, `prompt_digest`, the settings and windows used, the filters, the counts, the limitations, the trends) in `--state-dir` |

A reading is reused only when the events shown to it (`shown_hash`), `TREND_PROMPT_DIGEST` and the incident
`schema_version` are all unchanged; a changed window, a different set of events or a different incident contract is asked
again. A damaged `trends.jsonl` exits **5** and is not overwritten, a chain that stops answering leaves the remaining
candidates measured with no reading and exits **4**, and a candidate whose answer was unusable is a failure that exits
**1** — the run still reports what it measured.

**A counted increase is not an explanation.** `reading.title`, `reading.pattern` and `reading.explanations[]` are a model's
reading of numbers the pipeline measured; the console puts the explanations after `may fit instead:` and labels them
`(a reading of the numbers, not a record)`, and none of them is a finding, a cause or a confirmation. The words
`confirmed`, `statistically significant`, `proves`, `proof` and `caused by`
in any generated text — the group's own title included — demote the trend to `potential` by themselves. Nothing here tests
a hypothesis: there is no significance test, no confidence interval and no seasonality model, because these records do not
carry enough events to justify one, so the brief's rule against claiming significance without a suitable method is kept by
claiming nothing of the kind.

Limits on this stage: a rise is measured only over the records this run read, so an empty window cannot tell a quiet
district from one nobody filed, and a period the run never fetched is invisible to it rather than counted as zero; one
request shows at most **8** of a window's events and a run interprets at most **6** candidates, so the rest are measured
without a reading and reported as such — the counts stay whole, since they are arithmetic over every event, but a reading
may cover less than the window it is attached to; places are names at the level the source printed them and no boundary is
resolved; event counts are only as deduplicated as the saved link decisions are, and one undecided link is enough to refuse
an alert. All 35 tests use scripted interpretations and hand-built link decisions — no live trend accuracy is claimed.

## Collector Copilot

`--ask` answers a question in the language the asker writes it in, from the records a run already read. It is the fourth
post-step (`copilot.py`) and two model requests with a deterministic search between them: one reads the question into a
search (`copilot-1`, digest `3b3af481362e31d4`), the pipeline runs and measures that search with no provider in the loop, a
second words the answer (`answer-1`, digest `20ba0d754c1c4750`). It changes no Incident and needs no endpoint, chat service,
vector index, table or migration: every count, window, area, status and quotation it prints is measured from the records,
and each answer is a document written beside the checkpoint.

```bash
python -m intelligence.pipeline --all --state-dir runs/madurai --link-events \
  --ask "எந்த பகுதிகளில் தொடர்ந்து வெள்ளம் ஏற்படுகிறது?"
python -m intelligence.pipeline --all --state-dir runs/madurai \
  --ask "Which incidents need follow-up?" --ask "What changed this week compared with last week?"
python -m intelligence.pipeline --all --state-dir runs/madurai \
  --ask "Which unresolved incidents involve the revenue department?" \
  --ask "Which reports support the claim that Sholavandan was affected?" --json
python -m intelligence.pipeline --document notices/panchayat-note.pdf --ask "Summarize the evidence in this notice"
```

| Step | What happens |
| --- | --- |
| Question | `--ask`, repeated for more; a run reads at most **6** questions and answers at most **6**. `--ask` with nothing to read, and `--ask` beside `--since`/`--until`/`--category`/`--incident-type`/`--department`/`--min-severity`/`--place`, are refused at parsing: the question carries its own dates, places and filters, so a flag naming them again would be two searches |
| Reading | The first request is shown the shape of the corpus and none of its content: event/incident/record/publisher counts, the dates the records reach and how many events carry none, the place names as the records themselves write them (first **60**), the categories, event types, departments, severities and statuses present, how many records came from a document read locally, and the saved issue families and measured trends by id. The answer returns `intent` — one of `repeated_areas`, `open_followups`, `period_change`, `supporting_evidence`, `department_load`, `document_evidence`, `counting`, `other` — the places, category, incident type, department and minimum severity to search, `days_back` (1–365), whether to compare with the window before, and a restatement of what it searched for. Its schema holds no fact field, so a reading cannot answer |
| Held, not guessed | A token outside the printed lists, a place no record here names, a window in days outside 1–365 or with no dated record to count back from, a comparison with no window length, a reading that does not say back what it searched for, confidence under **0.8**: each is a finding, and any finding leaves the interpretation `review_required` while the search still runs on the parts that held. `Interpretation` refuses an accepted reading that carries a finding, a held one that says no reason, and `unresolved` outright — a reading is used or held, never quietly dropped |
| Search | Deterministic: accepted incidents only, `review_required` ones left out and counted in the limitations; distinct events from the saved `--link-events` decisions, so four reports of one flood are one event to a question as they are to a count; `recurrence.Filters` over category, type, department and severity and over the event's own date and its basis; places matched against the names the unit's own records carry, normalized and by substring. An empty result names which part failed — nothing read, nothing behind the filter, no record naming that place, no dated event in the window asked for — rather than printing a zero |
| Measured | Events, incidents, records and publishers; areas with their precision; undated events and events outside the window; follow-ups and recorded responses copied out of the `event_status` a report claimed for itself with its own quote, `source_url` and `retrieved_at`; the records that came from a document (`scanned_document`/`pdf` evidence); and with `compare`, the equal window before the one asked for, the change, and the areas appearing on one side only. At most **8** matched events are printed per request and at most **10** quotations per event; the rest are still counted, and a limitation says an answer may rest on fewer events than it counted |
| Answer | The second request is shown the numbered events with their own quotes, ids, day, place and status, the measured lines, and the saved work by id. It returns a headline, statements each labelled `source_fact` / `calculated` / `possible_explanation` / `recommendation`, caveats, the event indexes and evidence ids it rested on, and a confidence. The schema has no count field, so nothing it writes is used as a number, and at most **6** statements survive |
| Refusal | A statement with no words, a kind outside the four tokens, a `source_fact` that cites no quotation printed with it, a `calculated` statement naming no events or writing a digit no measured line printed for this question, an event index or evidence id that was never shown, a saved id the pipeline never printed — each is left out with its reason as a finding, and what remains is `qualified`, never repaired. `confirmed`/`proved`/`statistically significant`/`caused by` anywhere in the generated text adds a finding by itself. A `possible_explanation` or `recommendation` can never be accepted as a statement, an answer resting on a reading held for a person is held too, and one quoting fewer than **2** of the events it counted is a finding beside it. `Answer`'s and `AnswerStatement`'s validators refuse the same shapes on a stored line — a count that is not the id list it prints, a quotation of an incident never retrieved, a grounded answer carrying a finding — so a hand-edited `answers.jsonl` cannot arrive as a grounded answer. A question whose statements all fell out is `not_answerable` with `missing` naming what is absent, never answered by stretching a nearby fact |
| Quoted, not retyped | A citation is the record's own `Evidence` copied whole — quote, field, span, `source_url`, modality — selected by the evidence id the answer named, so a quotation in an answer cannot be a paraphrase and an id the search never printed has nothing to copy from |
| States | `grounded` (statements present and every one accepted, the reading and the answer both at confidence ≥ **0.8**, no finding, nothing held, at least **2** of its own events quoted where it counted that many) / `qualified` (an answer with its finding printed beside it, or one resting on a reading a person has not confirmed) / `not_answerable` (nothing stated, printed as `not answered:`). `accepted` here is this layer's grounding verdict, never a person's confirmation |
| Language | The script is read off the characters (`tamil`, `latin`, `mixed`, `other`) and the answer is required back in the language the question's main words are in, with place names and quotations left exactly as the records write them. Nothing is transliterated: a Tamil question is searched by the Tamil names the records carry, and a place named in a script they do not use matches nothing and says so |
| Stored | `answers.jsonl` (one `Answer` per question, rewritten whole, each carrying the interpretation it was searched with) and `copilot.json` (`schema_version`, both prompt versions and digests, the counts, the limitations, the answers) in `--state-dir` |

A saved reading is reused only when the question's own text hash and `COPILOT_PROMPT_DIGEST` are unchanged; an answer only
when, beside that, the incident `schema_version`, `ANSWER_PROMPT_VERSION`/`ANSWER_PROMPT_DIGEST` and the hash of the events
shown to it are unchanged. A run that has read more records asks the same question again against the bigger corpus, and a
question worded differently is a different `answer_id` and is asked from the start. A damaged `answers.jsonl` exits **5** and
is not overwritten; a chain that stops answering leaves the remaining questions carrying only what the search measured, with
`no reading was asked` named as the provider, and exits **4**; a question whose answer came back unusable is a failure that
exits **1**; and a question these records honestly cannot answer exits **0** — the refusal is the result, not an error.

**An answer is not a finding.** `possible_explanation` is a model's reading of the measured lines and `recommendation` is a
suggestion for a person; the console marks them `(a reading of the numbers, not a record)` and
`(a suggestion for a person, never recorded as done)`, and neither can be accepted on `AnswerStatement`'s own validator.
Work actually done appears only in `recorded`, copied from an `event_status` a report claimed for itself with the quote that
said it — a suggested action never joins that list. Access control stays the platform's: this layer answers from the records
the run was allowed to read and knows nothing about who asked (see [Integration contract](#integration-contract)).

Limits on this stage: answers cover only the records this run read, so an empty result says the records hold nothing, not
that the district saw nothing; places are matched by the wording the records themselves use, so a different transliteration
matches nothing; events are only as deduplicated as the saved link decisions are, and with none saved repeated reports count
separately; one request prints **8** of a search's events and one run reads and answers **6** questions, so a wide question
is measured whole and read partly; there is no conversation, no follow-up memory, no embedding retrieval and no second
framework — the brief's rule is kept by reusing the corpus, `Filters`, `event_units` and `Evidence` the earlier post-steps
already built, and the existing chain. All 45 tests use scripted interpretations and answers, hand-built link decisions and
a transport that fails the run if it reaches `GET /records` — no live answer accuracy is claimed.

## District Briefing

`--briefing` assembles the period a run read into one document a Collector can read without asking anything. It is the
fifth post-step (`briefing.py`) and **one** model request sitting after all the arithmetic: the window, the counts, the
places, the statuses, the saved recurring issues and the measured increases are worked out with no provider in the loop,
printed to the model as measured lines, and only then phrased (`brief-1`, digest `0b6f0fefb6212216`). It changes no
Incident and needs no endpoint, mailer, scheduler, table or migration — an email, a scheduled send or a dashboard tile
would read the `briefing.json` this step writes, and none of them is implemented here.

```bash
python -m intelligence.pipeline --all --state-dir runs/madurai --link-events --recurring-issues --emerging-trends \
  --briefing --brief-days 7
python -m intelligence.pipeline --all --state-dir runs/madurai --briefing --brief-language tamil
python -m intelligence.pipeline --document notices/panchayat-note.pdf --briefing --since 2026-09-01 --until 2026-09-30
```

| Step | What happens |
| --- | --- |
| Period | `--brief-days` (default **7**, max **365**) counts back from where the period ends: `--until` if it was given, otherwise the newest dated event's own day, with the basis it arrived on recorded in `window_basis`/`anchor_basis` (`record`/`given`/`named`/`none`). `--since` says where it starts instead, and is refused beside `--brief-days`; with no dated record anywhere the window is `the whole corpus, since no record here carries a date to count a period from`. `--brief-days`/`--brief-language` without `--briefing`, and `--briefing` with nothing to read, are refused at parsing |
| Counted | `record_kind = incident` only, accepted readings only, `review_required` left out of every count and reported; the same `recurrence.Filters` the earlier post-steps use narrow it; distinct events come from the saved `--link-events` decisions, so four reports of one flood are one event in a briefing as they are in a count; undated events fall in no period, and events outside it are counted and named as *not absent from the district — only from this briefing* |
| Items | The saved `IssueFamily`s and measured `Trend`s that are `accepted` **and** overlap the counted events, each as a `BriefItemRef` carrying only the event ids inside this period, with the sentence "Saved recurring issue ISS-… counts 3 of its 8 event(s) in this period" printed for the model to refer to by id. A family or trend waiting for a person never becomes an item |
| Status | `OpenFollowUp`s and `RecordedResponse`s are copied from the `event_status` a report claimed for itself with its own quote, `source_url` and `retrieved_at`; an incident whose status nothing evidenced raises `status_unstated`, a status with no words to quote raises `status_unquoted`, and neither is ever phrased as work done or as work owed |
| Carried | `IssueFamily.suggested[]` arrive beside the events that support them as `origin = saved_issue_family` actions — kept only when they cite a quotation this briefing counts and rest on an event of this period; an offered action that claims something was done, or writes a number no measured line printed, is not carried and says which of those it was |
| Asked | One request: the measured lines plus at most **6** events printed (`MAX_BRIEF_EVENTS`, each with its own quotations up to **10**), and it returns a headline, up to **6** lines tagged `situation`/`recurring`/`rising`/`open`/`recorded`/`documents`, up to **5** actions (`follow_up`/`verify`/`coordinate`/`monitor`/`other`), caveats and a confidence. Its schema holds no count, date or place field, so nothing it writes can become a number or a name — it only points at the event indexes, saved ids and evidence ids that were printed to it, and every figure the briefing shows was measured before it was asked |
| Refusal | A headline, line, action or caveat writing a digit no measured line printed; a past claim off `PAST_WORDS` (`completed`, `was done`, `action taken`, `was ordered`, `carried out`, `already repaired`, `resolved by`, …) or a `claim_words_in` demotion; an event index or evidence id that was never printed for it; a saved id the run never printed; a line or action that *names nothing it follows from*; a department no record of its own events names; a body written in another script than the one asked for — each is dropped with its reason as a finding, and what remains is `qualified`, never repaired |
| States | `issued` (every line and action accepted, the briefing and each of its parts at confidence ≥ **0.8**, nothing held, and at least **2** of its own counted events quoted where it counted that many) / `qualified` (a briefing with its findings printed beside it — including one that simply printed fewer of the events it counted) / `withheld` (nothing survived, or the period counted nothing: `missing` names what is absent, and the document states nothing and suggests nothing). `accepted` here is this layer's grounding verdict, never a person's confirmation |
| Stored | `briefings.jsonl` (one `Briefing` per line, rewritten whole, sorted by id) and `briefing.json` (`schema_version`, the prompt version and digest, the period, `days_back`, `language`, the counts, the limitations, and each briefing with its plain-text `document`) in `--state-dir` — the artifact a mailer or a dashboard would read, kept beside the checkpoint like every other post-step file |

`Briefing`'s own validator refuses a stored line the pipeline could not have produced: a `briefing_id` that is not the one
its period and items derive, an `events`/`incidents`/`records`/`sources` count that is not the id list it prints, a shown or
truncated event count that does not close, an item printed twice or an item whose events leave the period, a quotation
citing an event the briefing never counted, a line citing no quotation that was printed with it, a named saved item that was
not printed with it, an issued briefing still carrying a line held for a person, a briefing that says something was done when
no record here holds it, and a withheld briefing that states or suggests anything. So a hand-edited `briefings.jsonl` cannot
arrive as an issued briefing, and `load` exits **5** rather than rewriting it.

A saved briefing is reused only when its `briefing_id` — period, counted event ids, item ids and language — matches and
neither the `shown_hash` of the events printed to the model nor `BRIEF_PROMPT_DIGEST` moved, beside the incident
`schema_version`. A run that has read more records, or that changed the window or the filters, asks again; a briefing
request that came back unusable is a failure at exit **1**, a chain that stopped asking leaves the period measured and
unphrased at exit **4**, and a period these records cannot fill is `withheld` at exit **0** — the refusal is the result.
`--json` prints the whole document; the console otherwise prints the counts, the headline, each line under its section and
each action as `suggested (follow_up, briefing): … - follows measured_increase TRD-… (a person's decision, never recorded
as done)`.

**A suggested action is not work assigned.** `ActionOrigin.SAVED_ISSUE_FAMILY` and `ActionOrigin.BRIEFING` are kept apart
on every action, `method = llm` and `recorded = false` ride on the ones the model wrote, and work actually done appears only
in `recorded_responses[]`, copied from an `event_status` a report claimed for itself with the quote that said it. Nothing
this step produces can be read as a task given to a department: a `department` on an action survives only when one of the
events it follows already names it, and even then it names who the records implicate, not who was told.

Limits on this stage: a briefing covers only the records this run read, so an empty period says the records hold nothing for
it, not that the district saw nothing; places are the names the records themselves write, with no canonical or GIS-resolved
form (that stays GIS's, see *Boundaries*); events are only as deduplicated as the saved link decisions are, and with none
saved repeated reports count separately; one request prints **6** of a period's events, so a busy week is measured whole and
phrased partly; one run writes one period, and a week-over-week comparison belongs to `--emerging-trends`, whose saved
`trends.jsonl`/`TrendStore` this step reads rather than re-measures; there is no mailer, no scheduler, no push and no UI
here. All 78 tests use scripted briefings, hand-built link decisions, families and trends, and the transport that fails the
run the moment it reaches `GET /records` — no live briefing accuracy is claimed.

## Boundaries

Intelligence reads `CommonRecord`s. It does not own ingestion or normalisation, does not write PostgreSQL or run the
backend, and does not resolve geography: no canonical id, no coordinate, no taluk/block/village the record does not
name, no district-code or market-id lookup (see [Locations and the GIS hand-off](#locations-and-the-gis-hand-off)). `Location.resolution_state` is where GIS records the decision and `pending_gis` is this module's last
word. A feed's machine-readable geography stays a `context_fact` under the path the feed printed rather than being
relabelled, because picking it out would mean naming it. The `locations` a value-shaped record now carries are therefore a
hand-off, not a result: each says what the record declared, cites the field that declared it, and leaves
`canonical_location_id`, `latitude`, `longitude` and `confidence` null for GIS to fill — `pending_gis` on a place that came
from source metadata is a claim that a *name* was recorded, never that a boundary was resolved. Two schema gaps are left by
that rule rather than worked around: `MentionType` has no `market`, and `Location` has no state slot, so a market name keeps
kind `unknown` and a declared state stays in `source_geography`. Naming a `market` mention kind and a state slot is the
smallest contract change GIS may want, and it is Alagan's and Alex's call, not one this module makes alone. Nothing outside
`intelligence/` is changed to make this work, and no other team changes its contract to consume it.

### Locations and the GIS hand-off

A narrative record's places are read, not resolved. What a `Location` from the model holds:

| Field | Rule |
| --- | --- |
| `text` | The record's own words for the place, Tamil kept as printed. An English rendering the cited field does not carry moves to `normalized_name` and `text` takes the quote |
| `normalized_name` | The usual English form, a reading aid only |
| `role` | `event_location` the most specific place it happened, `event_container` a wider area around it, `reporting_origin` a dateline, `mentioned_only` anything else. A district named as a dateline or in passing is not where it happened |
| `district`/`taluk`/`block`/`village` | Kept only when the record's text or its declared `location.*` prints that value, or it is the place's own name at its own level. Otherwise dropped with `unsupported_claim` on `locations[loc-N].<level>` and the record goes to review |
| `canonical_location_id`, `latitude`, `longitude` | Never set here. An answer that supplies them is refused (`unauthorised_gis_value`) and the place stays `pending_gis` |

Linking only counts places with an event role, so two reports filed from Madurai are not paired for it.

An empty `locations` stays empty, and nothing copies the record's declared `location.*` into it: that geography is the feed's
own tag, and an auto-filled event place would hand every report from the same district a candidate to pair on, which is what
the role rule above exists to prevent. What the layer does instead is say so — a `generation.warnings` line naming that the
places the record declared about itself sit under `source_geography`, and on the console
`places=none in the account (declared: Madurai;Tamil Nadu)` — so an empty list reads as an account that evidenced no place,
not as a record with no geography. A place the account *does* evidence is kept in the script it is printed in; a Tamil village
name needs no Latin spelling to be accepted, and the map is still GIS's to make.

Limits: level support is a case-folded substring match, so `Melur` is also found inside `Melurpatti`; a level written in
one script for a place the text names in another (`Melur` for `மேலூர்`) is refused and left for a person; whether
"Madurai" means the city or the district is the model's reading plus review. No gazetteer exists, so nothing is
resolved and no place is matched to an alternative spelling.

**What GIS (Alagan) needs to provide** — as of 2026-10-11 no GIS code or reference data exists on any branch:

- A gazetteer, Madurai first: canonical id, level (district/taluk/block/village/town/locality), parent chain, Tamil,
  English and alternative names, and coordinates or a boundary.
- A resolver that reads `locations[]` with `resolution_state = pending_gis`, using `text`, `normalized_name`, the stated
  levels and the role, and writes back `resolved` (with id and coordinates), `ambiguous` (several candidates, for a
  person) or `rejected`.
- It must never rewrite `text`, `role`, `evidence_ids` or `review`, and never resolve a village the text does not name.

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
  `c9c277914ac07499`; this layer now asks `6e2f43b839df9fca`, and its deployed chain now names a Groq model rather than
  the Gemini pair that produced them, so a run over that state directory reads the narrative records again on two counts
  — the wording and the reader. They remain valid evidence of what the older wording and the older account produced, and of
  nothing else. The value-shaped half those runs used to reuse (`structured-1`, which neither a prompt change nor a
  provider change touched) has since been retired twice more — `structured-2` for the day/precision and
  declared-location work, `structured-3` for the declarations and the empty-field list this pass puts in provenance — so a
  run over those old state directories reprocesses them, which asks a model nothing and costs no quota, and its stored lines
  then carry the corrected semantics. That final sentence was written when only the mapping version had moved:
  `PROMPT_VERSION` and `PROMPT_DIGEST` have moved since — `stage-2.20`, `stage-2.22` and now `stage-2.29` — so a run over
  those old state directories re-asks their narrative half too, and that half is billed quota, not a free re-read.
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
- **A recovered classification shows up only on a fresh read.** This pass changed what assembly does with an answer, not the
  wording sent to a model: `PROMPT_VERSION` and `PROMPT_DIGEST` still read `stage-2.29` / `6e2f43b839df9fca`. A run pointed at
  a state directory that already holds `stage-2.29` lines returns those stored documents from the checkpoint — with their
  `incident_type = unresolved` and their empty `locations` — and asks nothing, so the recovered readings appear only in a new
  `--state-dir`, which is billed quota again. The offline suite replays the stored answer shapes through the new assembly,
  which proves this layer's decision and not what a live model returns next.

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
| 2.20 | Master-brief Stage 0/1, offline: a news record's own stamp (`published_at`) is never accepted as the event's time and stays in `provenance.declared_event_time`; `PROMPT_VERSION` → `stage-2.20` so readings stored under the old rule are re-read rather than reused; the 422 contract skew documented for the platform owner; `test_stored_report_quotes.py` grounds the real electric-shock report's stored Tamil quotes (the full article is not in the repository, so these are its quotes, not the article) | Complete (offline) |
| 2.21 | Master-brief Stage 2, offline: cross-record event linking (`--link-events`, `link-1`) — signal-and-time candidate blocking, pipeline-stated conflicts, one LLM comparison per pair, evidence-cited `EventLink`s kept beside the checkpoint in `links.jsonl`/`events.json`, review instead of forced merges, idempotent reuse; Incidents and the contract unchanged | Complete (offline) |
| 2.22 | Master-brief Stage 3, offline: Madurai-first location reading — administrative levels kept only when the record prints them, the source's own place words kept in `text`, place roles defined in rule 9, datelines and passing mentions no longer link reports, GIS requirements documented; `PROMPT_VERSION`/`PROMPT_DIGEST` → `stage-2.22`/`e731dc0cf60ebb08`, so stored semantic readings are read again | Complete (offline) |
| 2.23 | Master-brief Stage 4, offline: OCR and document intelligence (`--document`, `documents.py`) — Tesseract TSV and PDF text layers read into page fields through the ordinary semantic path, word and page confidence carried on the record, `scanned_document`/`pdf` evidence with engine notes, uncertain spans held for review as `uncertain_recognition`, missing language packs and image-only PDFs refused; `PROMPT_DIGEST` unchanged | Complete (offline) |
| 2.24 | Master-brief Stage 5, offline: the output pass for the Collector's interface — each distinction the brief lists mapped to the field or file that already carries it and pinned by `test_dashboard_output.py` (16 tests over one report, one forecast and one price line through both readers); `EventLink`, `EventGroup` and `events.json` stamped with the incident `schema_version`, and a comparison decided under another contract re-asked; "accepted is not confirmed" and "an incident count filters on `record_kind`" written down as the consumer's two rules; no new structure, no endpoint, no frontend, no action-tracking field | Complete (offline) |
| 2.25 | Master-brief Stage 6, offline: recurring issues (`--recurring-issues`, `recurrence.py`, prompt `issue-1`) — eligible incidents filtered first, distinct events from saved link decisions, deterministic place∧problem candidate blocking, one grouping request per candidate group and all counts, areas, distributions, changes and recorded responses computed arithmetically, safeguards that hold a questioned grouping for review, one event never a recurrence, recorded actions kept structurally apart from suggested ones, `issues.jsonl`/`recurring.json` beside the checkpoint; no endpoint, no table, no frontend, no button | Complete (offline) |
| 2.26 | Master-brief Stage 7, offline: emerging trends (`--emerging-trends`, `trends.py`, prompt `trend-1`) — the recent window measured against the equal windows back to back before it, distinct events from saved link decisions rather than articles, patterns counted by category, event type, place name and saved issue family, two coverage corrections a bare count cannot fake (events per record read, and reports per event plus publishers new to the window), all windows/counts/means/ratios arithmetic and the model asked only for meaning with no number in its schema, `potential` versus `alert_eligible` behind the thresholds with `Trend` re-deriving every figure it prints, the claim words that demote a trend by themselves, `trends.jsonl`/`trends.json` beside the checkpoint; no notification service, no dashboard, no endpoint, no table, no migration | Complete (offline) |
| 2.27 | Master-brief Stage 8, offline: the Collector Copilot (`--ask`, `copilot.py`, prompts `copilot-1` + `answer-1`) — one request reads a Tamil, English or mixed question into a search with no answer field in its schema, the search then runs with no provider over accepted incidents and saved `EventLink` units with `recurrence.Filters`, and a second request words the answer as `source_fact`/`calculated`/`possible_explanation`/`recommendation` statements; quotations are the record's own `Evidence` selected by id rather than retyped text, a calculated digit no measured line printed is refused, an answer quoting fewer than 2 of its own counted events is qualified not repaired, a question the records cannot answer is `not_answerable` with the reason and exits `EXIT_OK`; place matching stays wording-only (no transliteration invented), reuse is keyed on the question hash plus both digests and the incident `schema_version`, `answers.jsonl`/`copilot.json` sit beside the checkpoint; no endpoint, no chat service, no vector index, no table, no migration | Complete (offline) |
| 2.28 | Master-brief Stage 9, offline: the district briefing (`--briefing`, `briefing.py`, prompt `brief-1`) — one period measured with no provider in the loop (window from `--brief-days`/`--since`/`--until` with its basis recorded, distinct events from saved `EventLink`s, accepted incidents only, saved `IssueFamily`s and measured `Trend`s that overlap it, open follow-ups and recorded responses copied from a report's own `event_status` quote), one request that only phrases what it was shown and holds no count field, `IssueFamily.suggested[]` carried beside the events that support them as `origin = saved_issue_family` and drafted ones as `origin = briefing` with `recorded = false`, a number no measured line printed or a word saying something was done drops that sentence with its reason, an issued briefing needs every part accepted and 2 of its own events quoted, `briefings.jsonl`/`briefing.json` beside the checkpoint with `Briefing` refusing a hand-edited line; no email, no scheduler, no dashboard push, no endpoint, no table, no migration | Complete (offline) |
| 2.29 | The evidence-attribution and dynamic-assessment pass, offline: a quote the answer named one field for but which sits verbatim in exactly one other citable field is anchored in the field that really holds it and the move is disclosed in the report, while a tie between two carriers or a field that repeats the words with no offset to choose is still refused and words verbatim in no field are refused as `unsupported_claim` instead of the mislabelled `span_mismatch` they carried (`ambiguous_quote` where only a repeating field holds them); an optional scalar the record cannot answer — `priority` — is left absent with a warning instead of shipping an invalid unresolved claim plus a review-triggering issue that quarantined an otherwise correct reading, while an off-taxonomy token still fails as `invalid_value` and a doubted OCR span still carries its evidence into review; severity, priority, status and category are read from the whole article rather than from the headline phrase (rules 1, 3, 5), priority assessed on its own words and never copied from severity, an administrative request never proof that anything ended; a news record's publication stamp is refused as the event's time even when it arrives beside a real span; `PROMPT_VERSION`/`PROMPT_DIGEST` → `stage-2.29`/`6e2f43b839df9fca`; `SCHEMA_VERSION`, `CommonRecord`, `Incident` and every serialized payload shape untouched; `test_evidence_attribution.py` (14 tests) with 864 offline, no live call | Complete (offline) |
| 2.30 | The structured-display and recovered-citation pass, offline: a contextual line prints `n/a` for the six happening fields the contract itself refuses a value in (the document still carries `unresolved`/`unknown`/`null`) and gains `values=` for every `context_facts` entry and `places=` for the places the record evidenced, so an accepted market price or IMD forecast is inspectable on the console with no new field, no payload change and nothing reclassified — traced on the two records the brief names and proved display-only, conversion and serialization having kept the warning, both temperatures, the day and all eight price facts; a citation whose two runs are the record's own words but whose middle the answer rewrote is anchored on whichever of the stretches its runs bracket holds the most of the citation's own characters, the rewritten middle dropped and the move disclosed, which recovers a real live `hospital_service_gap` and a `reported` filed `unresolved` for their citation rather than their meaning — while two stretches that bracket it with equal weight are refused as `ambiguous_quote` with both offsets named and a bracket that is not unique, is under eight characters, over-reaches the citation, or is offered for `title`, `description` or a list item is still refused, and words verbatim in no field still fail as `unsupported_claim`; a reading that named no place keeps `locations` empty and says why instead of copying the feed's district into a role linking pairs on; `SCHEMA_VERSION`, `PROMPT_VERSION`/`PROMPT_DIGEST` and every payload shape untouched, so a stored `stage-2.29` reading is reused unchanged and only a fresh state directory shows the recovery; `test_summary_display.py` (10) and `test_recovered_citations.py` (10) with 884 offline, no live call | Complete (offline) |
| 10+ | Canonical location resolution (GIS — out of this module), and the outbound pieces that would read what is already written here: a mailed or scheduled briefing, a dashboard tile, a timeline, action tracking with a person's confirmation | Planned |

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
- **HTTP 422 `extra_forbidden` on `provenance.declared_event_time`, `declared_event_time_precision` or
  `fields_without_value` is contract-revision skew, not a backend schema.** `backend/app/api/incidents.py:10` validates
  the body with `intelligence.contract.Incident` itself; `origin/main` (80eb565) still carries the older `Provenance`
  with only `declared_severity`/`declared_status`, so a backend started from that checkout refuses what this branch
  writes. Required alignment (Alex): run the backend against an `intelligence/contract.py` that matches
  `feature/intelligence`, i.e. merge it before posting. The fix is not to drop the provenance keys or relax
  `extra="forbid"`; an old-contract backend would also fail to read such documents back on `GET /incidents`.
- **Event links have no backend endpoint yet.** `links.jsonl`/`events.json` live only in `--state-dir`; nothing is posted. Each line validates as an `EventLink` and each `events[]` row as an `EventGroup`, both carrying `schema_version` "2.0" like the incidents whose ids they name, so the read side is one file parse and one model validate — the Incident documents themselves are unchanged and still post as before.
- **Recurring issues have no backend endpoint either.** `issues.jsonl`/`recurring.json` are the same kind of artifact in the same directory: each line validates as an `IssueFamily` carrying `schema_version`, `prompt_version` and the `shown_hash` of the text it was decided from, so Alex's Recurring Issues panel reads one file rather than a new API, and no table, migration or second pipeline is required for it. The brief's expected result maps onto fields that already exist in these documents; the gap is only the transport, which belongs to the platform owner.
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

### What the Collector's interface can ask, and what answers it

| The screen needs | What answers it | Since |
| --- | --- | --- |
| Accepted, or confirmed by validation | `validation.state = accepted` with `validation.issues = []`, and `validation.validator` — `intelligence.intelligence` for a reading of prose, `intelligence.structured` for a copy of values | 1 |
| Waiting for a person, or left unanswered | `validation.state = review_required` / `unresolved`, one `validation.issues[]` row per field (`field`, `code`, `detail`), `claims[].review = unresolved` for a field nobody answered | 2.10 |
| A forecast or context instead of an incident | `record_kind`, `context_type`, `context_facts[]` | 2.17 |
| The source, the quote, the link, the timestamp | `evidence[]`: `quote` with `char_start`/`char_end` that replay against the field text, `field`, `source_url`, `raw_reference`, `retrieved_at`, `modality`; `provenance.*` for the record itself | 2.12 |
| Classification and severity where supported | `incident_type`, `category`, `department`, `severity`, `priority`, each with the `claims[]` row naming the quote that carried it — or the contract's absence value | 2.10 |
| Location and how precisely it is known | `locations[].district`/`taluk`/`block`/`village` only at the levels the source named, `resolution_state = pending_gis` with canonical id and coordinates null until GIS, `event_time_precision` for the time | 2.15, 2.22 |
| Duplicate or related reports | `links.jsonl` (one `EventLink` per pair) and `events.json` (`EventGroup`s) in `--state-dir`, every row stamped with the incident `schema_version` | 2.21, 2.24 |
| Recurring issue results | `issues.jsonl` (one `IssueFamily` per line) and `recurring.json` (filters used, counts, limitations, families) in `--state-dir`, from `--recurring-issues`: `events`/`incidents`/`records`, `per_month`, `first_event`/`last_event`/`span_days`, `recent`/`previous`/`change`, `areas` + `geographic_precision`, `references[]` with each incident's own `source_url` and `retrieved_at`, `recorded`/`followups`, `confidence`, `review`, `findings` | 2.25 |
| Emerging trend results | `trends.jsonl` (one `Trend` per line) and `trends.json` (settings, windows, filters, counts, limitations, trends) in `--state-dir`, from `--emerging-trends`: `dimension`/`value`, `status` (`potential` / `alert_eligible`) with `findings[]`, `events`/`incidents`/`records`, `arithmetic` (`blocks[]` with each window's own event/incident/record/publisher counts and the records the run held there, `baseline_mean`, `baseline_max`, `change`, `ratio`, the per-event and per-record rates, and the thresholds used), `areas` + `geographic_precision`, `references[]` with each incident's own `source_url` and `retrieved_at` and its event's `evidence_ids`, `deduplicated`/`links`/`undecided_links`, `reading` (title, pattern, `one_problem`, explanations, confidence) | 2.26 |
| Evidence-backed suggested actions | `IssueFamily.suggested[]` beside the events that support it: `action`, `department`, `event_ids`, `evidence_ids`, `confidence`, `review`, and `recorded = false` with `method = llm` so a suggestion can never be read as work assigned; recorded work stays in `recorded[]`/`followups[]`, copied from the `event_status` a report itself claimed | 2.25 |
| An answer to a question, in the asker's language | `answers.jsonl` (one `Answer` per question) and `copilot.json` (both prompt versions and digests, the counts, the limitations, the answers) in `--state-dir`, from `--ask`: `question`/`script`/`intent`, `state` (`grounded` / `qualified` / `not_answerable`) with `findings[]` and `missing`, the `interpretation` the search ran on, `events`/`incidents`/`records`/`sources` with their ids and urls, `areas` + `geographic_precision`, `recent`/`previous`/`change`/`comparison`, `followups[]`/`recorded[]`, `document_records[]`, `citations[]` with each quotation's own `field`, `source_url`, day and `modality`, `statements[]` with kind, `review`, confidence and the ids they rest on, `explanation` (headline, caveats, cited ids, confidence), `provider`/`model` | 2.27 |
| A district briefing for a period, with its next steps | `briefings.jsonl` (one `Briefing` per line) and `briefing.json` (prompt version and digest, period, `days_back`, `language`, counts, limitations, and each briefing with its plain-text `document`) in `--state-dir`, from `--briefing`: `briefing_id` derived from the period, counted event ids, item ids and language; `since`/`until`/`anchor` with `window_basis`/`anchor_basis` saying what the window was measured from; `state` (`issued` / `qualified` / `withheld`) with `findings[]` and `missing`; `events`/`incidents`/`records`/`sources` with their id lists and urls, `shown_events`/`truncated_events`; `areas` + `geographic_precision`; `items[]` (`recurring_issue` / `measured_increase`, each with only the event ids and quotations inside this period) and `named_items[]`; `numbers[]` (the only digits any sentence may write); `open_followups[]`/`recorded_responses[]`/`status_unstated`; `citations[]` with each quotation's own `field`, `source_url`, day and `modality`; `headline`, `lines[]` (section, body, the event and item ids it rests on), `actions[]` (`kind`, `origin` = `saved_issue_family` / `briefing`, `target_kind`/`target_id`, `department`, `event_ids`, `evidence_ids`, `confidence`, `review`, `recorded = false`, `method = llm`), `caveats[]`, `language`, `confidence`, `provider`/`model`, `prompt_version`/`prompt_digest`/`shown_hash` — and `filters`/`deduplicated`/`links`/`undecided_links`/`undated`/`outside_period`/`skipped_review`/`excluded_by_filter` so a zero can be shown as measured rather than as absence | 2.28 |

- **`accepted` is not "confirmed".** Nothing in this contract can say a person agreed with a reading: `ReviewState` holds
  exactly `accepted`, `review_required` and `unresolved`, the only validators that ever write it are the two readers named
  above, and `ExtractionMethod.HUMAN` exists in the vocabulary while this pipeline never writes it. An officially
  confirmed badge belongs to the platform's own review state, kept beside these documents rather than produced by them.
- **An incident count filters on `record_kind`.** `--post-incidents` sends accepted incidents, forecasts and market prices
  to the same `POST /incidents`, and `--state-dir` writes all three into one `results.jsonl`, so a count over either is a
  count of readings, not of incidents. The run report keeps them apart (`Accepted incidents` against
  `Accepted context/forecast`, `RunSummary.accepted_incidents` against `.accepted_context`); a consumer has to apply the
  same split before it prints a number.
- **A panel with no rows is not a finding of absence.** A recurring-issue run writes its accounting beside its families
  even when it filed nothing — `excluded_by_filter`, `skipped_review`, `undated`, `truncated`, `not_grouped` and an empty
  `issues` list in `recurring.json` — so a zero can be shown as "this is what was measured", not as the district having no
  problem. A trends run keeps the same rule beside its own trends: `trends.json` carries `Patterns counted`, `Candidate
  increases`, `Interpreted now`, `Measured without a reading`, `Events with no date`, `Events outside the windows` and the
  blocked-window wording, so "no alert-eligible trend" prints as what was measured — a rise below the threshold, a history
  too short to compare, a window the run read nothing in — and never as a district where nothing is happening.

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
run artifact, not an API. The five opt-in post-steps add files of the same kind beside it — `links.jsonl`/`events.json`
from `--link-events`, `issues.jsonl`/`recurring.json` from `--recurring-issues`, `trends.jsonl`/`trends.json` from
`--emerging-trends`, `answers.jsonl`/`copilot.json` from `--ask`, `briefings.jsonl`/`briefing.json` from `--briefing` — each
line validating against a model in this package, none of them an endpoint.

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
  `frontend/src/pages/Dashboard.jsx:270-279` is the same assumption in the market view: it maps `evidence[]` to
  `item.field || 'Source excerpt'` over `item.quote || 'No excerpt supplied'`, so a price line prints its field paths with
  no value beside any of them, while `context_facts` — which holds the commodity, both prices, the market, the unit and the
  quantity — is never read. And `Dashboard.jsx:281` hands `incident.severity` to `Shared.jsx:1-9`, which falls back to the
  literal token, so an accepted market-price or forecast record is tagged "unresolved" — the console's `n/a` rule and this
  module's refusal to invent a severity are the same decision, and the tag is the frontend's half of it: hide the indicator
  when `record_kind` is not `incident`, or label it from `record_kind`, rather than reading a contract value that means
  "this record states no happening" as a failed classification. None of this is an intelligence defect and none of it was
  changed here.

None of that is done here, and none of it can be: no file outside `intelligence/` was touched by this pass, so the
dashboard renders what it rendered before it. What changed is that the documents those views would filter on now carry
their own declarations, their empty-field list and their kind, and that a `--post-incidents` run writes them.

## Tests

```bash
python -m pytest intelligence/tests -q
python -m compileall intelligence
```

884 tests, all offline. `test_pipeline.py` 87, `test_batch.py` 80, `test_briefing.py` 78, `test_recurrence.py` 55, `test_spans.py` 54, `test_copilot.py` 45,
`test_llm.py` 45,
`test_fallback.py` 42, `test_orchestration.py` 36, `test_trends.py` 35, `test_documents.py` 33, `test_routing.py` 32,
`test_grounding_regression.py` 32, `test_gemini.py` 30, `test_linking.py` 25, `test_api_client.py` 20,
`test_contract.py` 20, `test_dashboard_output.py` 16, `test_groq_deployment.py` 15, `test_locations.py` 15, `test_geography.py` 14,
`test_evidence_attribution.py` 14, `test_evidence.py` 12, `test_offline_guard.py` 12, `test_summary_display.py` 10,
`test_title_fidelity.py` 8, `test_recovered_citations.py` 10, `test_prompt_budget.py` 6,
`test_stored_report_quotes.py` 3. No test touches
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

`test_summary_display.py` holds the console as a display of the document rather than a second report: a beetroot price line
and an IMD forecast day go through the deterministic reader with `NeverCalled`, and every `data.*` value the record printed
must stand on the line under its own `values=` pair, with the empty `data.forecast` and the null humidities named in
`provenance.fields_without_value` instead of erasing the facts beside them. It pins that the six happening fields print `n/a`
on those lines while the document still carries `unresolved`, `unknown` and `null`; that a warning with no prose around it is
kept as a `source_metadata` row with `quote: null`, `span_validation = not_applicable` and its own field hash; and that
neither line adds a key to any serialized model. `test_recovered_citations.py` replays the live Tamil reading's own shapes:
a citation whose opening and closing runs are the record's words but whose middle the answer rewrote comes back as an
evidenced `hospital_service_gap` and a `reported` status with no finding open, at `priority = high` on its own quote rather
than copied from the severity, with the village kept in Tamil and no canonical id, coordinate or confidence invented, and
`event_time` still refused because the only stamp that page carries is its own publication time. A citation whose closing run
the record never wrote is still `unsupported_claim`, its classification left `unresolved` and the record in review, and the
same words offered for `description` are refused outright — prose has no bracket to fall back on, and a paraphrase is not
evidence however neatly its ends match. Two more shapes settle the choice the live article needed: a citation whose
runs bracket one sentence by its whole tail and another by tail plus clause is anchored on the wider of the two, and
where two sentences bracket it with equal weight nothing is chosen — the value comes back `unresolved` with an
`ambiguous_quote` finding naming both stretches, so the refusal says which two sentences the record could not be made
to pick between.

`test_dashboard_output.py` states the contract rather than a behaviour: one waterlogging report, one IMD forecast and one tomato line go through both readers and must come back in one document shape; `accepted` and `review_required` may differ in nothing but `validation.state`, while a field nobody answered stays `unresolved` inside an accepted document; no produced document may hold a person's method or a confirmation state; every accepted quote must replay in the field it cites and every copied value must be cited by path, url and timestamp; a feed's own `Orange Alert` or `COMPLETED` must stay a declaration and never arrive as a severity or as action taken; a forecast and a price line must leave the incident total while staying postable beside it; and the saved links must come back as documents of the incident contract whose ids they name.

`test_recurrence.py` holds the recurrence rules as shapes rather than as prose: three reports of one flood that an
accepted link joined are one event in every count while the same three with no decision saved are three; two distinct
floods four weeks apart in one village become an accepted family whose `per_month` adds up to its own event count, while a
road accident in that same village is never even a candidate; two differently worded Tamil reports of one water problem —
a supply line that never filled and a borewell that ran dry — group on the model's word and keep both `incident_type`s, and
a shortage beside a canal breach stays apart even though the model is asked to group them, because the pipeline's own
shared-signal check refuses it. One distinct event cannot be filed at all: no bucket forms, an answer that groups one
event is demoted to a finding, and `IssueFamily` rejects the document on its own validator. Every reference must replay —
each quote is revalidated against the exact span in the record it names — and areas, dates, severity, category and the
two-window change are asserted to be arithmetic over the records, never text from the answer. A grouping below 0.8, one
that cites an id it was not shown, a department outside the taxonomy, an event missing from the `events`/`not_grouped`
split, or a difference the model itself listed is filed `review_required` with that reason and never accepted; an event the
records never dated, an invented village, an unreadable or damaged `issues.jsonl`, an empty dataset, a spent allowance and
a model that answered nonsense each have their own case, and the printed report is asserted line by line.

`test_trends.py` holds the same rule for the comparison: the windows must be equal and back to back or nothing is measured,
and a history that leaves fewer than two of them says how far back the records reach instead of inventing a zero. The
honesty gates each get their own case — a 4-article burst the run read more records for is corrected to its share of the
records read, the same 4 events with the baseline's own coverage stay a rise, twice the reports per event is read as more
reporting, a publisher the baseline never had cannot be told from a new problem, a window the run read no records for is
never a quiet zero, and events with no baseline at all are a new appearance rather than an increase. Every threshold is
moved by the operator in one case and the same dataset comes back the other way in the next. Four reports of one flood are
one event once a saved link decision joins them and three when none was saved, and the second is reported as
undeduplicated. A generated `confirmed`, `statistically significant`, `proves`, `proof` or `caused by` — in a title or an
explanation — demotes a trend that passed every numeric rule, an answer naming an event it was never shown is dropped and
recorded, a reading under 0.8 is named rather than quietly raised, and `one_problem: false` is honoured as "not one
pattern". `Trend` and `TrendArithmetic` are shown to refuse a hand-edited document: a rewritten count, a mean that is not
its windows', a window length that differs, an id list that no longer matches, an alert carrying a finding, a trend whose
references lost their quotes. The rest is the ordinary bookkeeping: the prompt asks for no number and the request prints the
windows it measured, an over-long window says what it cut without losing a count, the bounded number of interpretations per
run leaves the rest measured and unread, a saved trend is reused until its shown events or the contract move, a damaged
`trends.jsonl` exits **5** unchanged, a chain that stops answering exits **4** with the candidates still counted, an
unusable answer exits **1**, an empty or undated dataset builds no model at all. The CLI half drives `main` with a scripted
transport — measure, reuse on the next run, a blocked history reported without a fabricated zero, the exit ladder, and the
settings refused without `--emerging-trends` — which is the only way a bug that appears solely in a real run is caught: the
UTC default for `decided_at` was exactly one.

`test_copilot.py` holds the Copilot's rules as documents rather than as prose. Both prompts are pinned by digest and shown to
ask for no number of their own; a Tamil, an English and a mixed question are labelled as written, and a number written in
Tamil digits is the same number for the digit guard. The search is asserted deterministic — the same corpus and reading return
the same ids with no provider asked to retrieve — and it is a set of filters, never a filter string of its own: severity,
category and department narrow it, a window counts back from the newest dated record, a `context` or `forecast` record is
never answered from, an incident waiting for a person is left out of every search and counted in the limitations, and four
reports of one happening that a saved link joined are answered as one event. The gaps are each a case: a place no record
names costs no answer call, an undated event answers no question about a period, a window over records that carry no date is
dropped with the reason printed, an empty corpus never sends a question to a model. The cuts are printed as cuts — more events
than one request holds, more places than the reading is shown, more statements than an answer keeps. On the answer side a
`source_fact` needs its own quotation, an invented evidence id and a citation of an event never shown are named, `reports
prove something` never grounds an answer, a possible explanation and a recommendation are kept structurally apart from a
fact, saved work is citable only by the id printed, and a question read below 0.8 is still answered but held for a person.
The bookkeeping matches the earlier post-steps: a run answers only the questions it budgets and a seventh is refused rather
than asked, a blank or repeated question is dropped before anything is asked, an answer is reused while both shown hashes
hold and re-asked when the wording changes or a new record arrives, and a damaged `answers.jsonl` stops the run and is left
alone. The document half answers from pages read locally with their `scanned_document` evidence and says out loud when the
corpus is news instead. The console half drives `main`: two questions over the records it read, the search and the
uncertainty printed line by line, the flag conflict refused at parsing, a spent allowance deferring the rest at exit **4**, a
failed answer at **1**, a damaged saved answer at **5**, an honest refusal at **0**.

`test_briefing.py` holds the briefing's rules as documents rather than as prose. The prompt is pinned by digest and shown to
carry its own refusals as instructions — no number, no date, no place name, no past tense — and its schema is pinned to the
seven properties the pipeline can actually verify (`headline`, `language`, `lines`, `actions`, `caveats`, `saved`,
`confidence`), none of them a fact field. The request is shown to print the period with the basis it was counted from, the
counts, the places, the statuses and the saved work by id before any model is asked, with the sentence telling it that every
figure above was measured by the pipeline and not of it. The measuring half asks no model anything: a window counts back from
the newest dated event, from `--until` when one was given, and from `--since` when that was; an undated event counts in no
period; an event outside it is counted as outside rather than as absent; a `review_required` incident leaves every count,
cannot move the window, and appears in the limitations; a `forecast` or `context` record in the same run is never briefed
from; four reports of one happening that a saved link joined are one event in a briefing as they are in a count, and with no
link decisions saved the briefing says its own count may double what happened. The cuts are each a case: eleven events fall in
a period one request prints six of, and `shown_events`, `truncated_events`, the finding and the limitation all say so; a
saved issue waiting for a person, or one whose events all fall outside the period, becomes no item. On the phrasing side a
digit no measured line printed is refused in a headline, a line, a caveat and an action; the ten past-claim words each refuse
their sentence in all four places; a Tamil briefing asked in English, and an English one that answers in Tamil, are both
named; an event index or evidence id never printed for that line, a saved id the run never printed, a line that names nothing
it follows from, and a department no record of its own events names are each refused with their reason and leave the rest
standing. Carried actions keep their origin: a saved issue's own suggestion arrives as `saved_issue_family` with the
`event_status` quotes it was built on, and one citing no quotation this briefing counts, or resting on no event of this
period, is not carried at all. `Briefing` is then shown to refuse a stored line the pipeline could not have written — a
rewritten `briefing_id`, a count that is not its own id list, a printed-event total that does not close, the same item twice,
an item whose events left the period, a quotation of an event never counted, an issued briefing still holding a line for a
person, and a briefing that claims something was done — while a withheld one that states anything is refused too. The
bookkeeping matches the other post-steps: a briefing is reused while its period, its shown hash and its digest hold and
re-asked when the records or the window move, a chain that stops leaves the period measured and unphrased at exit **4**, an
unusable answer is **1**, a damaged `briefings.jsonl` is **5** and left alone, and an empty period is `withheld` at **0**. The
console half drives `main`: the briefing printed with its counts, headline, lines and suggested actions, the flag conflicts
refused at parsing (`--brief-days` beside `--since`, either without `--briefing`, filters without a step to count them), a
period the run named, and the document that a mailer or a dashboard would read.

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

`test_documents.py` never installs or runs an engine: a fake runner answers the exact argv Tesseract and `pdftotext`
would be called with, so the tests hold the TSV column contract (a wrong header, a short row, a non-numeric confidence
each refused), the offsets a page's words land at, the `-l tam+eng` / `-l eng` argument, and the refusals — a missing
language pack named before any OCR call, an unsupported suffix, an empty or absent file, an image with no recognised word,
a PDF whose every page is an image, an engine that cannot start. Through the ordinary path, a Tamil notice page becomes
accepted claims citing `data.page_001` at `modality = scanned_document` with the engine and file named in the evidence
note; the same page with `ராமசாமி` at confidence 41 keeps that evidence but lands `review_required` with
uncertain_recognition naming the word and its score, and an uncertain date leaves `event_time` null. A page whose mean
is under the floor sends every span on it to review. A PDF text layer cites `data.page_003` at `modality = pdf`, names
the page that has no layer in `fields_without_value`, and a declared-Tamil layer with no Tamil character is held as a
legacy font. The bookkeeping beside it: news prompts byte-identical with `document` absent from a normal record's dict,
a changed byte or engine a different hash, the same bytes passed twice read once, a checkpoint run that reuses without
asking, and a review-required reading that reaches neither `POST /incidents` nor the posted set.

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
