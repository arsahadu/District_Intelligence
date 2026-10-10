# District Intelligence — Intelligence Module

## Purpose

The AI/NLP layer of the Collector's District Intelligence Platform. It reads source-independent `CommonRecord` objects
and returns evidence-grounded `Incident`s: every field the pipeline asserts carries the quote it came from, or it says it
does not know.

**Intelligence lives entirely inside `intelligence/`.** It imports nothing from `ingestion/`, `backend/`, `frontend/` or
GIS — it reads records by declared field path against a structural protocol, so a renamed or missing field fails loudly
instead of surfacing as a silent `None`. Where it talks to the platform it uses HTTP: the orchestrator `GET`s
CommonRecords and, only when explicitly invoked with `--post-incidents`, `POST`s validated Incidents. It never opens a
database connection.

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
contract.Incident (schema 2.0)    — claims + evidence + validation + generation, one shape for both readers
```

Entry point: `python -m intelligence.pipeline`. Per-record boundary: `pipeline.process_record(record)`.

By default the CLI reads and reports only. Passing `--post-incidents` opts in to posting produced Incidents to
`POST /incidents`; `INTELLIGENCE_API_BASE_URL` configures the API origin and defaults to
`http://127.0.0.1:8000`. Duplicate ids are reported as duplicates, API validation/server errors and connection failures
are reported explicitly, and contextual/non-incident results are never posted. `context.build_context`,
`intelligence.extraction_request`, `intelligence.extract_incident` and `intelligence.validate_extraction` are callable
directly for stage-by-stage testing.

**One chain, three sources.** `data` is source-specific while the CommonRecord around it is not, so the context builder
discovers the keys a record actually carries instead of assuming a news-shaped one: a Dinamalar article offers
`data.content`, an IMD record `data.warning`/`data.forecast` and its temperatures, a market line `data.commodity`,
`data.market` and its prices. Strings and rendered numbers both become citable fields, so a quote of `50.0` from
`data.max_price` grounds exactly like a Tamil sentence. There is no `NewsPipeline`/`IMDPipeline`/`AgriPipeline` and no
field list per `source_type` — `source_type` and `record_type` reach the model as context, and the prompt tells it that
a field another source usually carries does not exist here unless it is printed.

| File | Responsibility |
| --- | --- |
| `records.py` | The record seam — the only place that knows how a CommonRecord is spelled: `read_record`, `input_hash`, field paths, read errors |
| `context.py` | `RecordContext`: every field the record carries as a citable `SourceField`, its hash, prompt block and provenance, plus its `location` slot kept out of the prompt |
| `router.py` | `Mode`, `route` — which reading one record gets |
| `structured.py` | The value reader: copies each field into a `context_fact`, files the record as `context`/`forecast`. No provider import, no prompt, no quota |
| `intelligence.py` | The semantic engine: request building, draft parsing, grounding, assembly |
| `llm.py` | `LLMProvider`, `LLMConfig`, `LLMRequest`/`LLMResponse`, `GeminiProvider`, `GroqProvider`, `OpenAICompatibleProvider`, `build_provider`, and the refusals read off what the endpoint answered: `QuotaExhaustedError` (allowance spent) vs `RateLimitedError` (window busy) vs `ModelUnavailableError`, `ProviderAuthError`, `EndpointUnreachableError`, `StructuredOutputError` |
| `fallback.py` | The model chain: `Chain`, `Candidate`, `chain_configs`, `classify` — which candidates exist, which share one allowance, and what each kind of refusal means for the next candidate |
| `geography.py` | The GIS boundary: `source_geography` — source values with field provenance; names no feed field, resolves nothing |
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
  incident still `review_required`. An abstention — no title offered at all — is left unfilled rather than quietly
  copied, so a blank is a stated absence and never a claim that a model wrote the headline.
- **Review states** — `accepted` / `review_required` / `unresolved`, with every finding carrying an `IssueCode`.
- **An event's clock, and which clock it is** — three sources, one field. A time the article's own words quote is
  evidence: `method = llm`, a cited span, `precision` as stated. A time equal to the record's own `event_time` is kept as
  `method = source_metadata` with no span and no evidence: the record's declared stamp, credited to the record rather
  than to the article, and on an incident named in `generation.warnings` because a news page's declared stamp is usually
  when the page was written. Its `precision` is what the answer stated, never inferred. A time equal to the instant the
  record was captured is refused outright and leaves `event_time` unresolved. Nothing borrows the retrieval time, and no
  accepted metadata stamp is ever reported as article-grounded.
- **Context facts copy their field** — a fact's `value` must be the characters its named field really holds. A
  translated, rounded or glossed value is refused as an `unsupported_claim` with the field's own words printed beside it,
  the fact keeps its real span, and the record goes to review: an accepted fact never says something its field does not.
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
- **Source geography** — the record's own `location.*` values handed to GIS as source values with field-level provenance.
- **Tamil, English and mixed text** — a classification quote proves the event happened; it does not have to contain the
  English token.

Deliberately not implemented: OCR, chatbot, deduplication, cross-record correlation, trends, timelines, storage writes.

## Input

HTTP only. `pipeline.py` `GET`s `/records` and `/records/{record_id}` from `INTELLIGENCE_API_BASE_URL`
(default `http://127.0.0.1:8000`). It never opens a database connection. Posting is disabled by default and only occurs with `--post-incidents`. Records are read
by declared field path against a structural protocol, so a renamed or missing field fails loudly instead of surfacing as
a silent `None`. `data` is source-specific, so both readers discover the keys a record actually carries:
`data.content` for an article, `data.warning`/`data.max_temp_c` for IMD, `data.commodity`/`data.min_price` for markets.

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
| `gemini` | Google's Gemini API through the Interactions endpoint over stdlib `urllib`: the credential in `x-goog-api-key`, `system_instruction`/`input` sent separately, the extraction schema sent as `response_format.schema`, and the response's own `status` read as its verdict — `incomplete` is truncation, never a half-written JSON to parse |
| `groq` | Groq through the official `groq` SDK, structured answers in strict JSON-schema mode |
| `openai_compatible` | chat-completions over stdlib `urllib`, `json_object` hint. What an unset `INTELLIGENCE_LLM_PROVIDER` means |
| `vllm`, `ollama` | the same class with its own default base URL |

Configuration, not code: `INTELLIGENCE_LLM_PROVIDER`, `_MODEL`, `_BASE_URL`, `_API_KEY`, `_TIMEOUT_SECONDS`,
`_MAX_RETRIES`, `_MAX_WAIT_SECONDS`, `_TEMPERATURE`, `_MAX_TOKENS`, `_REASONING_EFFORT`, plus
`INTELLIGENCE_API_BASE_URL`. The names are already stable; adding a provider means adding one class and one registry
entry. **Bedrock and SageMaker are not implemented.** No key is hardcoded anywhere: `api_key` is `repr=False`, `summary()`
reports only `api_key_present`, a transport error carries the status and message, never the request headers, and the
chain's report names the variable holding a credential rather than its value.

Gemini's own documentation names `GEMINI_API_KEY` for its credential, so that spelling is accepted as a fallback:
`INTELLIGENCE_LLM_API_KEY` is asked first and a deployment that already sets it keeps working unchanged, and a chain
entry's `api_key_env` still outranks both. A provider whose sends go through an SDK rather than this layer's socket —
`groq` — reports `accepts_transport = False`, so a chain told to answer one of those through an injected transport is
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

What this project deploys is `intelligence/chain.gemini.json`: two candidates on Google's Gemini API,
`gemini-3.8-flash` first and `gemini-3.5-flash-lite` second, each stating `provider: gemini`,
`base_url: https://generativelanguage.googleapis.com` and `api_key_env: GEMINI_API_KEY`, and inheriting every number
from `INTELLIGENCE_LLM_*` so one timeout and one wait budget govern both. `INTELLIGENCE_LLM_CHAIN_FILE` names it;
`--llm-chain intelligence/chain.gemini.json` names it for a single run. Paths open from the directory the run is started
in. A candidate whose provider needs a credential and whose `api_key_env` holds nothing is dropped before anything is
sent, so a deployment with no Gemini key anywhere asks nothing rather than inheriting `INTELLIGENCE_LLM_API_KEY` and
paying someone else for a refusal; with both candidates dropped, the run stops with `ProviderNotConfigured` naming the
file, each candidate and the variable that was empty.

Both models sit behind one endpoint and one variable, so they are one scope —
`https://generativelanguage.googleapis.com|GEMINI_API_KEY` — and one account's limits. A spent allowance, a full window,
a refused credential or a dead socket closes **both** on the first refusal: the second model is skipped with its reason
recorded in the checkpoint rather than sent to, and the record is deferred for a later run, so an exhausted month costs
one refusal for the whole batch instead of one per record. Only a model id that endpoint will not serve, or an answer
neither model can make readable, gives the fallback its turn. Switching models is not a way around an account-wide limit
and nothing here pretends it is.

`intelligence/chain.openrouter.json` is unchanged and still selectable — `qwen/qwen3.8-flash` then
`openai/gpt-oss-120b` on one `https://openrouter.ai|OPENROUTER_API_KEY` scope — and so is any single
`INTELLIGENCE_LLM_*` reader naming `openai_compatible`, `groq`, `vllm` or `ollama`. The migration changed what this
project asks by default, not what the layer can ask; the classes it no longer deploys are still tested.

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
python -m intelligence.pipeline --record-id NEWS-MDU-WLR-0001 --llm-chain intelligence/chain.gemini.json  # one record, the deployed chain

# a small re-read of particular records, pinned by id, into a state directory nothing has read into yet.
# --limit 6 would take the first six records the backend returned, which is not the same claim.
for id in NEWS-MDU-fbb1b8b1bd809612 NEWS-MDU-3eda6936fb2742e5 NEWS-MDU-caf0c933c1a793e2 \
          NEWS-MDU-d7e026050541d904 NEWS-MDU-37de736e0fb1c6cc NEWS-MDU-cc93365b11a62ad1; do
  python -m intelligence.pipeline --record-id "$id" --llm-chain intelligence/chain.gemini.json \
    --state-dir intelligence/runs/gemini-batch-6-fresh
done
```

The report prints the backend URL, how many were read and took each reader, then one line per result — id, title, kind,
type, category, department, severity, priority, status, event time, `facts=` count, review state with its finding count
and `path=structured`/`path=semantic` — grouped into accepted incidents, accepted context and forecasts, review-required
results, records deferred by a busy window or a spent allowance, records never sent because the run had already stopped
asking, failures, and the results a resume returned from the checkpoint without reading them again. When a chain ran, it
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
records were left deferred, **5** the checkpoint could not be trusted. `--limit` is client-side because `GET /records`
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
version (`structured-1` / `stage-2.12`), `schema_version`, `input_hash` and — for a semantic reading — `prompt_digest` all
match. `input_hash` is the whole read record, text and declared place, so a reissued record whose wording or district
changed is read again rather than answered from the store. `prompt_digest` is the same rule pointed at the prompt:
`PROMPT_DIGEST` in `intelligence.py` hashes the prompt text every narrative record is read with together with the schema
it is answered against, so rewording a rule changes the digest and what an older wording produced is read again — the
version label beside it no longer has to be moved for that to hold, which is exactly the gap that let a `stage-2.8`
reading be reused after the event-time rule changed. A semantic entry written before digests existed carries none and is read again
with that stated as the reason: this run cannot tell whether the wording still matches. A **semantic** result is also
reused only when this run could have produced it. On a chain, the entry's `provider`/`model` must be one of the
candidates this run asks — any of them may answer for a given record, so a reading by any of them is a reading this run
trusts. Without a chain, `pipeline.provider_identity` must name the same model, each side named by the configuration it
runs on rather than by the provider class — `vllm` and `ollama` are one class serving two endpoints — and a known
difference on either half re-reads the record. Structured results carry no provider, model or digest and are never
invalidated by one — a copy is a copy whoever runs it, and they key on `MAPPING_VERSION`. Moving
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
relabelled, because picking it out would mean naming it. Nothing outside `intelligence/` is changed to make this work,
and no other team changes its contract to consume it.

### What this pass did not fix

- **One `event_status` field answers two questions.** The contract has a single status for an incident, so "the protest
  was held" and "the drain is still blocked" cannot both be stated. Nothing here splits them and nothing infers one from
  the other: prompt rule 4 says a protest, a strike, a court hearing, an inspection or a meeting is an event, not an
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
  `c9c277914ac07499`; this layer now asks `5c051cba8233914c`, so a run over that state directory reads the narrative
  records again and reuses only the value-shaped ones (`structured-1`, which no prompt change touches). They remain
  valid evidence of what the older wording produced, and of nothing else.

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
| 4+ | Entity/location resolution hand-off, evidence revalidation, dedup, trends, briefing, OCR, chatbot | Planned |

Bedrock and SageMaker remain unimplemented — one provider class and one registry entry each. The chain asks only
candidates a configuration verified: it never invents a model id, and never assumes that two models behind one endpoint
and one credential hold separate allowances.

`GeminiProvider` reaches the API over stdlib `urllib` rather than through `google-genai`: the SDK is not installed here,
the layer already needed a bounded-wait send policy and a status code it can classify, and adding a requirement to read
one documented endpoint buys nothing. Every request field is built in one place, so a field Google renames is a
correction in one function rather than a migration.

## Integration contract

- Storable columns include `incident_id`, scalar fields, `event_time`, `provenance.*`, `validation.state`, and
  `generation.model` / `prompt_version` / `input_hash`.
- `to_storage_document()` serialises the whole incident; `Incident.model_validate_json` reads it back with its
  validators intact.
- **View Source** depends on `provenance.source_url` and `raw_reference` being preserved, and accepted claims' evidence
  ids resolving to spans that replay against stored field text.
- `review_required` needs human confirmation. `unresolved` and `unknown` are non-authoritative: absence of a finding,
  not a finding of absence.
- The API client communicates over HTTP: it reads CommonRecords and can optionally post validated Incidents.
  Intelligence never writes to PostgreSQL or connects to the database itself.

## Tests

```bash
python -m pytest intelligence/tests -q
python -m compileall intelligence
```
python -m compileall intelligence           # clean
```

`test_pipeline.py` 81, `test_batch.py` 76, `test_spans.py` 54, `test_llm.py` 43, `test_fallback.py` 42,
`test_gemini.py` 30, `test_grounding_regression.py` 32, `test_orchestration.py` 21, `test_contract.py` 19,
`test_routing.py` 18, `test_evidence.py` 12, `test_offline_guard.py` 12, `test_geography.py` 10. No test touches a real
provider or a live backend: prose runs use the `Scripted` double from `tests/builders.py` plus the counting, refusing and
out-of-quota doubles in `tests/test_batch.py`, value runs use `NeverCalled`, which fails the test the moment anything asks
a model a question, and every checkpoint file is written under pytest's `tmp_path`.

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

Offline over the October 2026 Madurai capture (`ingestion/data/normalized`, read straight off those files with no model
and no backend): 1,415 records — 1,345 agriculture, 43 news, 27 weather — route 1,372 `STRUCTURED` / 43 `SEMANTIC`; the
structured half processes in 0.99 s, all 1,372 come back `accepted` with 10,868 `context_facts` and 4,116
`source_geography` entries, and no incident carries a canonical id or a coordinate. Through the Stage 3 scheduler at that
same volume (the real price line under 1,372 distinct ids, a `--state-dir` in a temporary directory, no model configured,
no provider built): cold **9.5 s** at `--batch-size 25` (**2.0 s** in one batch, since every batch boundary rewrites the
whole `results.jsonl`), resume **1.0 s** with all 1,372 reused and nothing read again, 17 MiB of stored Incidents.
