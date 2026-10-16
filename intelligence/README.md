# District Intelligence — Intelligence Module

The AI/NLP layer of the Collector's District Intelligence Platform. It reads source-independent `CommonRecord` objects and
returns evidence-grounded `Incident`s: every field the pipeline asserts carries the quote it came from, or it says it does
not know. The long-form record of every pass — root causes, refusals, measured counts, live-call disclosures and the test
inventory — is in [`STAGES.md`](STAGES.md). This file is what an operator or a new reader needs in order to run the module and
reason about what it will and will not claim.

| Owner | Responsibility |
| --- | --- |
| Ahad — Data Integration | Source → `CommonRecord` |
| Prem — Intelligence | `CommonRecord` → validated `Incident` (this module) |
| Alagan — GIS | Place mentions → canonical geography, coordinates, visualisation |
| Alex — Platform | `Incident` → PostgreSQL, FastAPI, Collector workspace |

Everything lives inside `intelligence/`. Nothing is imported from `ingestion/`, `backend/`, `frontend/` or GIS; records are
read by declared field path against a structural protocol, so a renamed or missing field fails loudly instead of surfacing as
a silent `None`. Platform traffic is HTTP only — `GET /records`, and `POST /incidents` when `--post-incidents` is asked for.
No database connection is ever opened.

## The honesty rules this layer is built on

- A claim is filed only when its quote is verbatim in the field it cites, at the character offsets it names — Unicode
  characters of that field, never bytes and never UTF-16 units.
- A quote the record does not carry, a field it does not have, a band its taxonomy has no name for, a near-miss offset, and an
  inflected restatement are all refused. Nothing is repaired to fit.
- A feed's own `Orange Alert`, `UPDATED` or `COMPLETED` is a declaration: preserved in `provenance.declared_*`, never mapped
  into `severity` or `event_status`. A publication stamp is not an event time — `declared_event_time` says whose clock it is
  and the happening stays undated.
- `unresolved` and `unknown` mean absence of a finding, not a finding of absence. `review_required` means a person has to
  decide, and nothing here records a decision or claims one was made.
- A forecast and a market price are read for their values with **no model call at all** and keep their own `record_kind`.
  They are never turned into incidents so a dashboard column looks populated.
- Every count, mean, ratio, window and distribution a report prints is arithmetic over records. A model is asked for wording
  only, in a schema with no number field, and a digit no measured line printed is refused. An answer about the records and an
  explanation beside them stay structurally apart: `source_fact` and `calculated` lines cite evidence,
  `possible_explanation` and `recommendation` lines cite nothing.
- Reuse is keyed on what the model saw — a changed reading, a reworded prompt or a different incident contract asks again. A
  damaged store refuses the run at exit **5** and is left alone. A spent allowance, a busy window, a refused credential or a
  dead endpoint stops the asking, defers what is left, and says so; it never silently walks to another vendor.

## Pipeline

```
Platform API  GET /records             — one CommonRecord per object, read once per run
  ↓
pipeline.run_pipeline                  — batches of --batch-size, progress flushed at each boundary;
  ↓                                      one reader per run, built only if a narrative record is left
pipeline.process_record                — the one-record boundary
  ↓
router.route                           — a row of field values, or prose to be read for meaning?
  ↓ STRUCTURED                ↓ SEMANTIC
structured.process_record     context.build_context → extraction_request → provider → extraction
  ↓                                      (grounding, validators, review state)
Incident (schema_version "2.0")         — accepted, or review_required with every finding named
  ↓
checkpoint and reports → optional --post-incidents → Platform API  POST /incidents
                   → optional post-steps: --link-events, --recurring-issues,
                     --emerging-trends, --ask, --briefing
```

## Files

| Group | Files | Responsibility |
| --- | --- | --- |
| The document | `contract.py`, `models/` | The `Incident` (`SCHEMA_VERSION = "2.0"`), its taxonomies, `Provenance`, `Validation`, `Evidence`, and the validators that refuse an ungrounded claim. `StrictModel` is `extra="forbid"`. |
| The two readers | `records.py`, `router.py`, `structured.py`, `context.py`, `intelligence.py`, `spans.py` | The record boundary by declared field path, the structured/semantic decision, the deterministic value reader (`MAPPING_VERSION = "structured-4"`), the extraction request and its grounding, offset arithmetic on the pinned Unicode-character convention. A `source_metadata` item cites the field's own text at `[0, len(text))` and validates, exactly like a quoted claim. |
| Geography | `geography.py` | Source geography → `locations` at `pending_gis`; canonical ids and coordinates stay GIS's. |
| Providers | `llm.py`, `fallback.py`, `chain.*.json` | `openai_compatible`, `groq` and `gemini` classes, the `intelligence/.env` read, and the chain: candidates, scopes, one send per candidate, refusal classification. `chain.groq.json` is deployed; `chain.gemini.json` and `chain.openrouter.json` stay selectable. |
| Orchestration | `pipeline.py`, `api_client.py`, `checkpoint.py`, `posting.py`, `snapshot.py` | Batching, the report and the exit ladder, `GET /records` and `POST /incidents` with a round-trip check, the resumable store and its manifest cross-checks, `posted.json`, the verified `--newly-ingested` selection. |
| Post-steps | `linking.py`, `recurrence.py`, `trends.py`, `copilot.py`, `briefing.py` | The five optional passes and their stores beside the checkpoint. `recurrence.Filters` is reused by the four after it. |
| The chat | `chat.py`, `chat_service.py` | The engine — routing, three paths, value composition, a reply that refuses itself — and `chat-service-12`, `GET /health` and `POST /chat` over a stdlib `ThreadingHTTPServer` that writes nothing. |

## Two readers, one contract

A price line, a forecast and a news report all come back as the same `Incident` document. The structured reader
(`structured.py`) copies `data.*` values with **zero** model calls, so a table of value-shaped records costs nothing to read;
the semantic reader (`intelligence.py`) asks one model for meaning and then grounds every field it answers. A contextual
record keeps `record_kind = context`/`forecast` and its permitted `context_type`, and the contract itself refuses a value in
the six happening fields (`incident_type`, `category`, `department`, `severity`, `priority`, `event_status`) for a record
that states no happening — which is why the console prints those six as `n/a` on a market or weather line while an incident
prints its values or the honest `unresolved`, the serialized document carrying what it carries either way.
`test_dashboard_output.py` pins one document shape across both readers.

## Model, chain and configuration

`llm.py:43` reads **`intelligence/.env`**, not the repository root's. Two consequences of that cost the most debugging time
when they are unknown:

- python-dotenv loads with `override=False`, so a variable you `export` in the shell wins over the file.
- a process reads the file **once, at start**. Replace a key and the running pipeline or chat service keeps answering with
  the credential it already holds. Restart it.

`.env.example` documents every line and says which flag overrides it. The short form:

| Variable | What it does |
| --- | --- |
| `GROQ_API_KEY` | The only credential the shipped chain sends. It travels in a request header, never a URL, and is never written into a log, checkpoint, report or browser response. |
| `INTELLIGENCE_LLM_CHAIN_FILE` | `intelligence/chain.groq.json` — one candidate, on purpose. Relative paths open from the directory the run starts in, so run from the repository root or write it absolute. |
| `INTELLIGENCE_LLM_TIMEOUT_SECONDS`, `_MAX_RETRIES`, `_MAX_WAIT_SECONDS`, `_TEMPERATURE`, `_MAX_TOKENS`, `_REASONING_EFFORT` | The request budget every candidate inherits unless it states its own. `MAX_WAIT_SECONDS` caps the waiting on one busy window; `MAX_TOKENS` is the room the thinking *and* the answer share on Groq; `low` is the effort `openai/gpt-oss` honours there. |
| `INTELLIGENCE_LLM_PROVIDER`, `_MODEL`, `_BASE_URL`, `_API_KEY` | The single reader used when no chain file is named. `INTELLIGENCE_LLM_API_KEY` **outranks** `GROQ_API_KEY` for it, so a Gemini or OpenRouter key left there would be sent to Groq as the run's credential; keep it empty while the provider is `groq`. |
| `INTELLIGENCE_API_BASE_URL` | Where `--post-incidents` sends (`pipeline.py:127`). The chat service does not read it. |
| `INTELLIGENCE_CHAT_HOST`, `_PORT`, `_ORIGINS`, `_API_URL` | `chat_service.py` only. An empty value **in the file** falls back to the default (`chat_service.py:455`), so `--api-url` with no value is the only way to turn the dashboard read off. |

A chain's scope is `endpoint|credential-variable`. A refusal that vendor makes — spent allowance, busy window, refused
credential, unreachable endpoint — closes every candidate in that scope together for the run, which is why a fallback across
vendors is a file you edit rather than something that happens by default. Only a refusal about the model itself, or about an
answer that cannot be read, gives the next candidate its turn. Bedrock and SageMaker remain unimplemented: one provider class
and one registry entry each.

Current prompt identity, as the code computes it:

| Request | Version | Digest |
| --- | --- | --- |
| Extraction | `stage-2.29` | `6e2f43b839df9fca` |
| Chat routing — records or general | `route-1` | `1e90272db9278dad` |
| Copilot question read | `copilot-6` | `d72ed86219d64762` |
| Copilot answer wording | `answer-1` | `20ba0d754c1c4750` |
| Value-answer composition | `value-5` | `6c7404bbf5e21ed6` |
| General background answer | `general-5` | `a3d01bc03fd26e18` |
| Event comparison | `link-1` | `b7298b247ef1b2c4` |
| Recurring-issue grouping | `issue-1` | `6a0e74acd2859e76` |
| Trend interpretation | `trend-1` | `dcd6650c67435249` |
| Briefing phrasing | `brief-2` | `ab6db9ec4909d6a2` |

`chat-1` is an identifier only; that path builds no model request of its own.

## Running it

```bash
python -m intelligence.pipeline                     # safe default: five records, nothing written to disk
python -m intelligence.pipeline --record-id WEATHER-MDU-03-Oct
python -m intelligence.pipeline --all --json        # every record, full Incident documents

python -m intelligence.pipeline --all --state-dir runs/madurai              # resumable, 25 per batch
python -m intelligence.pipeline --all --state-dir runs/madurai --semantic-concurrency 4
python -m intelligence.pipeline --all --state-dir runs/madurai --no-resume  # read everything again
python -m intelligence.pipeline --mark-baseline --state-dir runs/madurai            # once, before the first daily run
python -m intelligence.pipeline --mark-baseline --state-dir runs/madurai --baseline-older-than 2026-10-10
python -m intelligence.pipeline --newly-ingested --state-dir runs/madurai --post-incidents  # the daily run

python -m intelligence.pipeline --all --state-dir runs/madurai --link-events --recurring-issues \
  --emerging-trends --briefing --brief-days 7
python -m intelligence.pipeline --all --state-dir runs/madurai --ask "Which incidents need follow-up?"
python -m intelligence.pipeline --all --state-dir runs/madurai --briefing --brief-language tamil \
  --since 2026-09-01 --until 2026-09-30 --category water
```

`--limit` is client-side because `GET /records` has no paging, and it is positional: `records[:limit]` of whatever order the
backend returned, default 5. `--limit 6` therefore means the first six records in that order and promises nothing about which
ids they are. A run that must see particular records names them with `--record-id`; `--all` clears the limit and refuses to
be combined with `--limit`; `--newly-ingested` selects by arrival and is the one selection that never takes the default five.
`--since`, `--until`, `--category`, `--incident-type`, `--department`, `--min-severity` and `--place` narrow what a post-step
counts and are refused when no step asked for them.

The report prints the backend URL, what each reader took, then one line per result: id, title, kind, `context=` (`-` on an
incident line), the six happening fields, `facts=` and a `values=` pair for every context fact, `places=` — or the honest
`places=none in the account (declared: Madurai;Tamil Nadu)` — review state with its finding count, and
`path=structured`/`path=semantic`. When a chain ran it also prints `Model chain`, `Chain scopes` (each endpoint with the
variable naming its credential, never a value), `Read by`, `Read by a fallback`, `Read by an earlier run`, and `Chain turns`
giving every refusal that cost a candidate its turn. `Read by` counts only what this run asked; readings returned from the
checkpoint are counted apart, so a resumed run cannot report a copy as a send. The counts close on themselves —
`fetched = selected + not selected`, `selected = reused + attempted + never sent` — so nothing deferred or unread is ever
reported as processed.

Exit codes: **0** nothing failed (a contextual result is processed, not a failure) · **1** a record could not be processed ·
**2** backend failure · **3** no provider could be built *and* the batch asked for one · **4** the provider refused further
reads and narrative records were left deferred · **5** the checkpoint could not be trusted · **6** a record
`--post-incidents` was told to send came back rejected or unreadable (a `409` duplicate is not this failure).

## Post-steps

Each one reads what the run already wrote, needs its own flag, and is reused only while the text shown to the model, its
digest and the incident `schema_version` all hold.

| Flag | Module | Artifacts | What it counts on |
| --- | --- | --- | --- |
| `--link-events` | `linking.py` | `links.jsonl`, `events.json` | One comparison per candidate pair. A pair not compared keeps its events apart, and the report then says `Distinct events: at most N`. |
| `--recurring-issues` | `recurrence.py` | `issues.jsonl`, `recurring.json` | Distinct events from the saved link decisions. One event is never a recurrence, and `recorded[]` and `suggested[]` stay structurally apart. |
| `--emerging-trends` | `trends.py` | `trends.jsonl`, `trends.json` | The recent window against the equal windows back to back before it, with two coverage corrections a bare count cannot fake. |
| `--ask` | `copilot.py` | `answers.jsonl`, `copilot.json` | Accepted incidents and saved link units; the search itself asks no provider. |
| `--briefing` | `briefing.py` | `briefings.jsonl`, `briefing.json` | One measured period; the request only phrases what it was shown and holds no count field. |

The exit ladder is the same for all five: a damaged store exits **5** and is never overwritten, a chain that stops answering
leaves the rest `not compared`/`not grouped`/unphrased and exits **4**, an unusable answer is a failure that exits **1** while
the run still reports what it did answer, and a question these records honestly cannot answer exits **0** — the refusal is
the result, not an error.

## Resume, quota, the daily run, posting

The checkpoint holds one line per record with a manifest beside it; fourteen ways the two can fail to account for each other
are each refused before the backend is read. A busy window or a spent allowance stops the asking for the rest of the run,
marks every unfinished narrative record `deferred`, keeps every structured result and every Incident already read, flushes,
and exits **4** — claiming no reset time, because nothing in the response establishes one. `--newly-ingested` diffs the
returned ids against `ingested.json` in its own `--state-dir`, because no timestamp reaches the truth: `GET /records` exposes
no `created_at`, and `retrieved_at` is naive-local from one feed and UTC-aware from another, while `event_time` says when the
happening occurred rather than when it arrived. With neither a snapshot nor a checkpoint to seed from, the run refuses
**before** touching the backend rather than reprocessing the whole table. `--mark-baseline` answers the first run: one
`GET /records`, no provider built, no record read, nothing posted — it settles every id whose arrival stamp falls before
`--baseline-older-than` (today by default) and prints what each unmarked row is still owed. Arrival clocks are consulted once
and only in the direction that keeps a record selectable, so a row with a naive, blank or unparseable stamp stays in the daily
run's way; a cutoff past today is read as the operator's explicit instruction to settle the whole table, undated rows included.
A `review_required` record never joins `ingested`: it is recorded in a separate `awaiting` list, still selected on every later
run and counted apart as `Records already awaiting a decision`, until a person's decision turns it into a completed reading —
because a `--no-resume` pass over the same record is free on the structured path and the posting ledger already refuses a
duplicate by digest. `--post-incidents` sends accepted incidents and nothing else — a forecast, a market line or any other
context record is read, kept in the checkpoint and answered by the chat, but `/incidents` holds incidents — never a
`review_required`, `deferred` or failed result, and it keeps one digest per incident id
in `posted.json`, so an unchanged reading is never re-sent across runs while a refused send is not remembered and is retried.

## The Collector's chat

`chat_service.py` serves `chat.py` over `127.0.0.1:8010` (stdlib, no framework) on the same `INTELLIGENCE_LLM_*`
configuration as the pipeline. It writes nothing: no `POST` anywhere, no checkpoint write, no key past the process.

| Path | Calls | What it answers from |
| --- | --- | --- |
| `record_values` | 0–1 | Runs **first**, unchanged. A weather or price question copies an accepted `forecast`/`context` record's own `data.*` values; at most one request supplies the wording, and any `composition_fault` — a citation out of range, a digit or place its cited facts do not print, a sentence joining more than three facts, a duplicated sentence, a printed fact nobody cites — drops the whole wording back to the records' own sentences and says which. |
| `record_search` | 3 | `route-1` says the question needs records → `copilot-6` reads it into a search → the search runs with no provider → `answer-1` words the answer. |
| `general` | 2 | `route-1` says it does not → `general-5` answers as marked background. Every line is a `possible_explanation` at `review_required`, cites no record and lists no id; `ChatReply` refuses to construct such a reply if it claims to be grounded. |

The routing flag is believed only when it says `needs_records === false`; an unset flag, a provider that never answered, or a
schema refusal searches the records and says so in the reply's own limitations — a search that finds nothing costs one more
question, while an answer from memory that should have read the records cannot be taken back. History is kept server-side per
`session_id` (`history_turns = 6`, `history_chars = 1200`, `turn_chars = 320`) as plain asked/answered lines: no citation, no
evidence id, no other session's text, and never on the value path, whose call count is an invariant. Limits: 400 characters
per question, at most 5 model calls per request, 20 questions per session per 600 s, 8 records and 12 facts shown. A bare
request for more — `tell in detail` — re-enters the earlier question's own records at **0** calls.

```bash
python -m intelligence.chat_service --state-dir runs/madurai
python -m intelligence.chat_service --state-dir runs/madurai --llm-chain intelligence/chain.groq.json
python -m intelligence.chat_service --state-dir runs/madurai --api-url ''    # stored readings only
python -m intelligence.chat_service --state-dir runs/madurai --origins http://localhost:5175

curl -s http://127.0.0.1:8010/health
curl -s -X POST http://127.0.0.1:8010/chat -H 'Content-Type: application/json' \
  -d '{"question":"What is the tomato price today?","session_id":"s-1"}'
```

The corpus is the `--state-dir` readings merged with the dashboard's own `GET /incidents` (15 s, `http://127.0.0.1:8000` by
default), so what the Collector's screen lists is what the chat can answer about. A list that cannot be reached, answers a
non-list, or holds a row the contract refuses is stated in `dashboard_read` and in the reply's limitations rather than
quietly narrowing the corpus. `/health` reports the service, prompt and schema versions and digests, what is answerable with
no model, the limits, corpus counts, `dashboard_read`, the origin allowlist, and `model.chain_state` — each endpoint with the
*variable* naming its credential and which of them refused, never a value. CORS answers only origins on the allowlist:
`localhost` and `127.0.0.1` on 5173 and 5174 by default, `INTELLIGENCE_CHAT_ORIGINS` or `--origins` for whichever port Vite
actually moved to.

**A closed chain is rebuilt between questions.** `fallback._note` closes a scope for the run, and a service outlives one
question, so `reopen_chain()` (`chat_service.py:212`) drops the memo when no candidate is left, called before each ask and
never inside one. Without it a single busy window silenced every later question with *"every candidate in the chain is already
closed"* — at zero billed calls — until the process was restarted.

The panel is `frontend/src/components/ChatWidget.jsx` with `frontend/src/services/chat.js` (`VITE_CHAT_BASE_URL`), mounted by
`App.jsx`. It shows the state chip, the headline, the statement lines, collapsed source evidence, and a refusal's own reason —
never the disclosure lists, which `--ask` and the service payload still carry in full.

Deliberately not built: no `/document` path and no upload UI (OCR and document reading were cut from the brief; the reader
is now deleted from this tree — `documents.py`, its test file, the Tesseract and `pdftotext` engine paths and the
`INTELLIGENCE_TESSERACT_CMD`/`INTELLIGENCE_PDFTOTEXT_CMD` variables, with no `--document` flag left in the parser to
remove; stage rows 2.23 and 2.33 in `STAGES.md` record what was built and then removed, and 2.44 the deletion), no writes of any kind, no authentication layer, no
vector index, no endpoint in `backend/`, and no second routing call to make routing free — a memo that charged a call the
provider had not made was built, then removed, because `ChatReply`'s own guard exists to stop exactly that dishonest count.

## Boundaries

Canonical ids, coordinates and boundaries are GIS's. A `location` at `pending_gis` with `method = source_metadata` is a place
the record declared, not one anyone verified, and resolving it must not rewrite `text`, the cited evidence or the `review`
state the source value arrived with. Open items worth knowing before anything depends on them:

- `named_places` (`chat.py:207`) matches Latin script only, so `மதுரையில்` narrows nothing and a Tamil value question reads
  every market instead of the one it named.
- The panel prints statement lines as HTML, which collapses the leading spaces the payload keeps, so a code answer needs
  `white-space: pre` in `App.css`.
- A question that needs records *and* explanation is answered one way only: a reply carries one
  `prompt_version`/`prompt_digest` and the `/chat` payload may not gain fields.
- `--ask` caps at six questions per run; that bound never applies to the chat service.

## Stage status

Every row of the table below is recorded at length, with its root cause and measurements, in [`STAGES.md`](STAGES.md).

| Stages | Work | Status |
| --- | --- | --- |
| 1–2e | `Incident` contract, evidence-first validation, provider abstraction, real extraction, runnable orchestration | Complete |
| 2f–2g | Two readers; source geography and the GIS resolution boundary | Complete |
| 3–3f | Batching, checkpoint and resume, deferral on a busy window or spent allowance, the configurable fallback chain, the deployed readers (OpenRouter, then Gemini, then Groq), prompt compaction, write-back from both collections | Complete |
| 2.10–2.18 | The live-reading refusals (a repeated subject word, a translated fact, a metadata clock), the offset convention pinned, title fidelity against scraped-page furniture, forecast date semantics, the accepted-context write-back that 2.43 narrowed back to incidents, provenance preservation | Complete |
| 2.20–2.33 | Master-brief stages 0–9: event linking, location reading, the output contract, recurring issues, emerging trends, the Copilot, the briefing — plus evidence attribution, structured display, cross-run posting idempotency and the verified daily-run selection | Complete |
| 2.34–2.40 | The Collector's chat: engine, service and dashboard panel, the general route, follow-ups and history, value composition behind its `composition_fault` gate, and the `route-1` call that decides records or general before anything expensive | Complete |
| 2.41 | A closed chain rebuilt between questions, the `.env` audit and rewritten `.env.example`, this split into an overview plus a long-form record | Complete |
| 2.42 | The daily run made safe to leave running: `--mark-baseline` settles history from one read without a model call, `review_required` stays owed rather than becoming ingested, and source-metadata evidence cites its own field text so no excerpt is blank | Complete (offline) |
| 2.43 | `--post-incidents` sends accepted incidents and nothing else — a forecast or a market line is still read, counted, checkpointed and answered by the chat, it is just never written to `/incidents`; and the Tamil literals five files had lost to a cp437 re-save are repaired byte-for-byte, with no detection rule, fixture value or expectation changed | Complete (offline) |
| 2.44 | The document reader is deleted rather than deferred: `documents.py`, its 42 tests, the `--document` path and the Tesseract/`pdftotext` settings are gone, with the one routing seam that could send a local document to the model, and the suite that watched for the module's absence goes with it | Complete (offline) |
| 10+ | Canonical location resolution (GIS), and the outbound pieces that read what is already written here — a mailed or scheduled briefing, a dashboard tile, a timeline, action tracking with a person's confirmation | Planned |

## Integration contract

Two rules for any consumer of these documents:

- **Accepted is not confirmed.** `validation.state == "accepted"` means this pipeline grounded its own claims. Nothing here
  records a person's decision, and no field should be rendered as one.
- **An incident count filters on `record_kind`.** The stored rows also hold forecasts and context records; counting them as
  happenings inflates every district figure the platform prints.

Beyond those: `to_storage_document()` serialises the whole incident and `Incident.model_validate_json` reads it back with its
validators intact; `event_time` must be shown with its `event_time_precision`, since `2026-10-03T00:00:00` at `precision =
day` is the forecast's *day* and rendering it as a clock reading is where the honesty of that field is lost; `provenance`
carries `declared_severity`, `declared_status`, `declared_event_time`, `declared_event_time_precision`, `input_hash` and
`fields_without_value`, so a reader can tell a blank in the source from a refusal by the reader and neither from an invented
value; `View Source` depends on `provenance.source_url` and `raw_reference` surviving and on accepted evidence ids replaying
against stored field text; and a `review_required` document is not authoritative.

What the platform owner still has to do, each one a capability this module is not permitted to build for itself:

| Gap | Evidence | Smallest contract |
| --- | --- | --- |
| `POST /incidents` returns 422 `extra_forbidden` on the three newer provenance keys | `backend/app/api/incidents.py:10` validates the body against `intelligence.contract.Incident`, while `origin/main` still carries the older `Provenance` | Run the backend against a `contract.py` matching `feature/intelligence`. Not: dropping the keys, or relaxing `extra="forbid"` — an old-contract backend would also fail to read such documents back. |
| `GET /records` cannot say which rows are new, and cannot page | `backend/app/api/records.py:63-66` returns the whole table ordered by a `created_at` the model holds but `CommonRecordResponse` does not expose | `created_at` as an extra response key, plus optional `limit`, `offset` and `order`, with today's behaviour as the default when they are absent. |
| The dashboard's running origin is refused by the backend | `backend/app/main.py:17-21` fixes three origins; Vite moves to 5174 whenever 5173 is taken, so `/incidents` reads as down in the browser while `curl` answers | Two strings added to that list, or a comma-separated `BACKEND_CORS_ORIGINS` with today's three as its default. The chat service already allows both ports. |
| Links, issue families and trends have no read side | `links.jsonl`, `events.json`, `issues.jsonl`, `recurring.json`, `trends.jsonl`, `trends.json`, `briefings.jsonl`, `briefing.json` live only in `--state-dir`, each line validating as its own document and carrying the incident `schema_version` | One file parse plus one model validate. No table, migration, second pipeline or new endpoint is required. |

## Tests

1,106 offline tests in `intelligence/tests/`, all of them passing. None of them can reach a live endpoint. A session fixture replaces name
resolution, socket creation, `connect`, `connect_ex` and datagram sends with one refusal, so a Gemini, Groq, OpenRouter,
OpenAI or backend request cannot happen by accident whatever transport it arrived on — with `HTTP_PROXY`/`HTTPS_PROXY` set
as well, because a dead proxy stops a send only when the code chooses to honour it. Every `INTELLIGENCE_*` and `*_API_KEY`
variable is stripped before the first test and `llm.DOTENV_PATH` is repointed at a file that does not exist, so no test can
load the repository's live credentials however it constructs its configuration. Because `load_dotenv` writes into live
`os.environ`, a function-scoped fixture takes the environment back after every test, and the suite is verified running
forwards and with every module in reverse.

`python -m pytest intelligence/tests -q` is the whole gate, and `python -m compileall intelligence` stays clean. What each
suite holds — the contract it pins per file, and the six Madurai records replayed offline — is in
[`STAGES.md`](STAGES.md#tests).
