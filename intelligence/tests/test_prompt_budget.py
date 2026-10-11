from __future__ import annotations

import json
import re

from intelligence.intelligence import (
    REQUEST_SCHEMA,
    answer_shape,
    prompt_text,
    prompt_vocabulary,
)

PROMPT_CEILING = 15_000
RULES_FLOOR = 9_000

GROUNDED = "object{value, field, quote, char_start, precision, confidence}"


def head() -> str:
    return prompt_text().split("Rules:\n")[0]


def rules() -> str:
    return prompt_text().split("Rules:\n")[1].split("Answer shape, typed")[0]


def vocabulary() -> str:
    return prompt_text().split("Tokens per enumerated field:\n")[1]


def shape_lines() -> list[str]:
    return [line[2:] for line in answer_shape().split("\n") if line.startswith("- ")]


def ref_of(spec: dict) -> str:
    if "$ref" in spec:
        return spec["$ref"].rsplit("/", 1)[-1]
    for option in spec.get("anyOf") or []:
        if found := ref_of(option):
            return found
    return ref_of(spec["items"]) if "items" in spec else ""


def test_the_prompt_sits_under_the_ceiling_and_its_rules_over_the_floor():
    said = prompt_text()
    assert len(said) <= PROMPT_CEILING, (
        f"the ask grew to {len(said)} characters; every rule added to it is paid for on every record "
        "every candidate is sent, so a new rule has to replace one rather than join them"
    )
    assert len(rules()) >= RULES_FLOOR, (
        "the rules are the whole refusal policy of this layer - what counts as evidence, which span "
        "proves which field, when to abstain - so a compaction pass that drops the block below "
        f"{RULES_FLOOR} characters has removed a behaviour, not a restatement"
    )


def test_the_answer_shape_is_still_derived_and_names_every_field_once():
    named = [
        part.strip()
        for line in shape_lines()
        for part in line.split(": ", 1)[0].split(", ")
    ]
    assert sorted(named) == sorted(REQUEST_SCHEMA["properties"]), (
        "the shape the model is asked to answer must cover exactly the fields the answer is validated "
        "against: a field it never heard of is answered blind, and a field it heard of twice is a hint "
        "that the shape stopped being generated from the draft"
    )


def test_the_answer_shape_prints_the_shared_signature_once_and_the_drafts_own_keys():
    said = answer_shape()
    assert said.count(GROUNDED) == 1, (
        "nine fields answer with the same grounded object; printing the key list nine times was the "
        "bulk of this block, so a second copy means the shape was written out by hand again"
    )
    grouped, *rest = shape_lines()
    assert grouped.startswith(
        "title, description, incident_type, category, department, severity, priority, event_status, "
        f"event_time: {GROUNDED} | null"
    )
    for field in ("locations", "entities", "relationships", "context_facts"):
        line = next(item for item in rest if item.startswith(f"{field}: "))
        keys = re.search(r"object\{([^}]*)\}", line).group(1).split(", ")
        assert keys == list(REQUEST_SCHEMA["$defs"][ref_of(REQUEST_SCHEMA["properties"][field])][
            "properties"
        ]), f"{field} is asked for keys the draft does not carry"


def test_the_token_fields_are_asked_for_a_bare_token_once_and_never_an_object():
    said = prompt_text()
    assert said.count("bare token") == 1
    assert 'not {"value": "incident"}' in said
    line = next(item for item in shape_lines() if item.startswith("record_kind"))
    assert line.split(":")[0] == "record_kind, context_type", (
        "record_kind and context_type are the two fields a strict endpoint will accept as plain "
        "strings; grouping them is what let the warning be printed once"
    )


def test_the_compacted_prompt_states_each_instruction_once_across_head_rules_and_shape():
    said = prompt_text()
    assert said.count("validated against") == 1, (
        "the head used to promise the schema and the shape block repeated the promise; the shape block "
        "is the one that prints the schema's types, so the head defers to it"
    )
    assert "the schema your answer is validated against" not in head()
    assert rules().count("empty") == 1, (
        "rule 9 keeps the one statement that an empty list is a real answer for a record naming "
        "nothing, while the shape block says a list field is empty when the record carries none; a "
        "third copy across rule 13 is the restatement this pass removed"
    )
    assert "empty when the record carries none" in answer_shape()
    assert "and page furniture describes no event" not in said, (
        "rule 11 opens on the metadata statement and later lists the furniture it refuses; the preview "
        "sentence said neither and was cut"
    )


def test_the_vocabulary_block_is_still_the_only_channel_for_the_allowed_tokens():
    assert json.dumps(REQUEST_SCHEMA, sort_keys=True).count('"enum"') == 0, (
        "the draft constrains no field with enums, which is why the tokens below are the taxonomy"
    )
    said = prompt_text()
    for field, tokens in prompt_vocabulary().items():
        assert f"- {field}: " in vocabulary(), f"{field} lost its token list"
        for token in tokens:
            assert token in said, f"{field} no longer offers {token}"
    assert len(vocabulary()) > 3_000, (
        "the schema carries no enums, so these 200-odd tokens are the entire taxonomy a semantic "
        "answer is allowed to pick from; shortening this block deletes required behaviour"
    )
