# District Intelligence — Intelligence Module

## Purpose

The AI/NLP layer of the Collector's District Intelligence Platform. It reads source-independent `CommonRecord` objects
and returns evidence-grounded `Incident`s: every field the pipeline asserts carries the quote it came from, or it says it
does not know.

| Owner | Responsibility |
| --- | --- |
| Ahad — Data Integration | Source → `CommonRecord` |
| Prem — Intelligence | `CommonRecord` → validated `Incident` (this module) |
| Alagan — GIS | Place mentions → canonical geography, coordinates, visualisation |
| Alex — Platform | `Incident` → PostgreSQL, FastAPI, Collector workspace |

## Pipeline

```
Platform API  GET /records                    — one CommonRecord per object
  ↓
pipeline.run_pipeline → pipeline.process_record
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
  │                        LLMProvider.generate_structured — groq SDK or any OpenAI-compatible endpoint
  │                        ↓  ExtractionDraft (all-optional wire model, extra keys ignored)
  │                        intelligence.validate_extraction → _assemble — span replay, coercion, ids
  └────────────┬───────────┘
               ↓
geography.source_geography        — what the record declared about place, as source values
               ↓
contract.Incident (schema 2.0)    — claims + evidence + validation + generation, one shape for both readers
```

Entry point: `python -m intelligence.pipeline`. Per-record boundary: `pipeline.process_record(record)`.

| File | Responsibility |
| --- | --- |
| `records.py` | The record seam — the only place that knows how a CommonRecord is spelled: `read_record`, `input_hash`, field paths, read errors |
| `context.py` | `RecordContext`: every field the record carries as a citable `SourceField`, its hash, prompt block and provenance, plus its `location` slot kept out of the prompt |
| `router.py` | `Mode`, `route` — which reading one record gets |
| `structured.py` | The value reader: copies each field into a `context_fact`, files the record as `context`/`forecast`. No provider import, no prompt, no quota |
| `intelligence.py` | The semantic engine: request building, draft parsing, grounding, assembly |
| `llm.py` | `LLMProvider`, `LLMConfig`, `LLMRequest`/`LLMResponse`, `GroqProvider`, `OpenAICompatibleProvider`, `build_provider` |
| `geography.py` | The GIS boundary: `source_geography` — source values with field provenance; names no feed field, resolves nothing |
| `spans.py` | Span arithmetic and replay: `SourceField`, `find_spans`, `locate`, `build_evidence`, `verify_evidence`, `revalidate` — the only sanctioned producer of `Evidence` |
| `contract.py` | The stable output: `Incident`, `Claim`, `Location`, `Entity`, `Relationship`, `SourceGeography`, `ContextFact`, `Provenance`, `Validation`, `Issue` and their enums |
| `pipeline.py` | The orchestrator: `process_record`, `fetch_records`/`fetch_record`, `run_pipeline`, `print_report`, `main` |
| `models/` | Shared pydantic vocabulary: `base.py` (`StrictModel`, `Confidence`), `enums.py`, `evidence.py` |
| `tests/` | The suite, plus `record_fixtures.py` and `builders.py` (the scripted and refusing provider doubles) |

## Current capabilities

- **Incident extraction** — title, description, event time and precision, locations, entities, relationships.
- **Classification from the contract's enums** — `incident_type`, `category`, `department`, `severity`, `priority`,
  `event_status`. The prompt's token lists are generated from those enums; no keyword or cue dictionary exists.
- **Evidence and provenance** — a quote is replayed against the untouched field text, cut to a span, and its id derived
  there. An invented or ambiguous quote is refused, not repaired. Whole source trail on `provenance`.
- **Review states** — `accepted` / `review_required` / `unresolved`, with every finding carrying an `IssueCode`.
- **Record kinds** — `incident`, `forecast`, `context`, `unresolved`, so a sapota price line and a flood share one
  contract without either being a lie.
- **Structured processing with no model** — market lines and weather days are copied, not interpreted.
- **Source geography** — the record's own `location.*` values handed to GIS as source values with field-level provenance.
- **Tamil, English and mixed text** — a classification quote proves the event happened; it does not have to contain the
  English token.

Deliberately not implemented: OCR, chatbot, deduplication, cross-record correlation, trends, timelines, storage writes.

## Input

HTTP only. `pipeline.py` `GET`s `/records` and `/records/{record_id}` from `INTELLIGENCE_API_BASE_URL`
(default `http://127.0.0.1:8000`). It never opens a database connection and never posts anything back. Records are read
by declared field path against a structural protocol, so a renamed or missing field fails loudly instead of surfacing as
a silent `None`. `data` is source-specific, so both readers discover the keys a record actually carries:
`data.content` for an article, `data.warning`/`data.max_temp_c` for IMD, `data.commodity`/`data.min_price` for markets.

## Output

`contract.Incident`, `schema_version = "2.0"`, 24 fields: provenance, the claimed scalars, `record_kind` /
`context_type`, `locations` / `entities` / `relationships`, `source_geography`, `context_facts`, `evidence`, `claims`,
`validation`, `generation`. `to_storage_document()` serialises it; `Incident.model_validate_json` reads it back with its
validators intact. The contract refuses what a model might have invented: coordinates or a `canonical_location_id`
without `resolution_state = resolved`, accepted items citing nothing, dangling evidence references, duplicate ids, a
`SourceGeography` entry produced by a model rather than copied from a field, and `accepted` while issues are present.
`unresolved` and `unknown` are legal values — the absence of a finding, not a finding of absence.

## Routing

`router.route` decides from the record's own metadata and shape: a `source_type` of `agriculture`/`weather` whose `data`
is a non-empty mapping of single-line scalar values is `STRUCTURED`. A `data.content` body, a nested value, a multi-line
value, an empty `data` or a feed nobody has met is `SEMANTIC`. Uncertainty resolves toward the model, never toward a
table, because flattening prose into field copies would report a text as if it had been read. `structured.py` and
`geography.py` never import `llm.py`, so a structured record cannot reach a provider even by accident. A run builds a
provider once, and only when a narrative record is in the batch.

## Current provider

| Config | What it does |
| --- | --- |
| `groq` | Groq through the official `groq` SDK, structured answers in strict JSON-schema mode |
| `openai_compatible` (the default) | chat-completions over stdlib `urllib`, `json_object` hint |
| `vllm`, `ollama` | the same class with its own default base URL |

Configuration, not code: `INTELLIGENCE_LLM_PROVIDER`, `_MODEL`, `_BASE_URL`, `_API_KEY`, `_TIMEOUT_SECONDS`,
`_MAX_RETRIES`, `_TEMPERATURE`, `_MAX_TOKENS`, `_REASONING_EFFORT`, plus `INTELLIGENCE_API_BASE_URL`. The names are
already stable; adding a provider means adding one class and one registry entry. **Bedrock, SageMaker and multi-model
fallback are not implemented.** No key is hardcoded anywhere: `api_key` is `repr=False`, `summary()` reports only
`api_key_present`, and a transport error carries the status and message, never the request headers.

`llm.py` reads the **repository root** `.env` (`load_dotenv(override=False)`), so an exported `INTELLIGENCE_LLM_*`
variable always wins; `intelligence/.env.example` lists placeholders only and `intelligence/.gitignore` keeps any local
`.env` out of the repository. An incomplete environment raises `ProviderNotConfigured` naming the missing variable.

## Running it

```bash
python -m intelligence.pipeline                    # safe default: five records
python -m intelligence.pipeline --limit 20
python -m intelligence.pipeline --record-id WEATHER-MDU-03-Oct
python -m intelligence.pipeline --all --json       # every record, full Incident documents
```

The report prints the backend URL, how many records were read, how many took each reader, then one line per result — id,
title, kind, type, category, department, severity, priority, status, event time, `facts=` count, review state with its
finding count and `path=structured`/`path=semantic` — grouped into incidents, contextual records and failures. Exit codes:
**0** no record failed (a contextual result is processed, not a failure), **1** at least one record could not be
processed, **2** backend failure, **3** no provider could be built *and* the batch asked for one. `--limit` is applied
client-side because `GET /records` has no paging.

## Boundaries

Intelligence reads `CommonRecord`s. It does not own ingestion or normalisation, does not write PostgreSQL or run the
backend, and does not resolve geography: no canonical id, no coordinate, no taluk/block/village, no district-code or
market-id lookup. `Location.resolution_state` is where GIS records the decision and `pending_gis` is this module's last
word. A feed's machine-readable geography stays a `context_fact` under the path the feed printed rather than being
relabelled, because picking it out would mean naming it. Nothing outside `intelligence/` is changed to make this work,
and no other team changes its contract to consume it.

## Stage status

| Stage | Work | Status |
| --- | --- | --- |
| 1 | LLM foundation, `Incident` contract, evidence-first validation, provider abstraction | Complete |
| 2–2e | Real extraction, runnable orchestration, accuracy and semantic reasoning | Complete |
| 2f | Two readers: `router.py` + `structured.py`, no provider or quota for value-shaped records | Complete |
| 2g | Source-aware structured data and the GIS resolution boundary: `geography.py`, `Incident.source_geography` | Complete |
| 3 | Bulk processing: batching, checkpoint/resume, quota-aware throughput | Planned |
| 4+ | Entity/location resolution hand-off, evidence revalidation, dedup, trends, briefing, OCR, chatbot | Planned |

## Next

Stage 3 only: batching over `GET /records`, checkpoint and resume, quota-aware processing (the narrative half of a batch
is the only part that spends anything). Multi-model provider fallback and Bedrock come later, as extensions of the
existing `LLMProvider` registry.

## Tests

```bash
python -m pytest intelligence/tests -q      # 230 passed (2026-10-09)
python -m compileall intelligence           # clean
```

`test_pipeline.py` 66, `test_spans.py` 54, `test_llm.py` 30, `test_contract.py` 19, `test_orchestration.py` 21,
`test_routing.py` 18, `test_evidence.py` 12, `test_geography.py` 10. No test touches a real provider: prose runs use the
`Scripted` double in `tests/builders.py` and value runs use `NeverCalled`, which fails the test the moment anything asks
a model a question.

Offline over the October 2026 Madurai capture (`ingestion/data/normalized`, read straight off those files with no model
and no backend): 1,415 records — 1,345 agriculture, 43 news, 27 weather — route 1,372 `STRUCTURED` / 43 `SEMANTIC`; the
structured half processes in 0.99 s, all 1,372 come back `accepted`, filed `context`/`market_price` or
`forecast`/`weather_forecast`, holding 10,868 `context_facts` and 4,116 `source_geography` entries, and no incident
carries a canonical id or a coordinate.
