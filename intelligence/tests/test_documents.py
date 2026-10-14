from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Sequence

import pytest

from intelligence import documents
from intelligence.checkpoint import fingerprint
from intelligence.context import build_context
from intelligence.contract import IssueCode, ReviewState
from intelligence.documents import (
    ENV_PDFTOTEXT_CMD,
    ENV_TESSERACT_CMD,
    TSV_COLUMNS,
    DocumentError,
    document_record,
    parse_tsv,
    read_document,
)
from intelligence.intelligence import PROMPT_DIGEST, extract_incident, extraction_request
from intelligence.models.enums import ExtractionMethod, Modality
from intelligence.pipeline import EXIT_DOCUMENT, EXIT_OK, main
from intelligence.pipeline import ENV_API_BASE_URL
from intelligence.records import RecordShapeError, read_record
from intelligence.tests.builders import NeverCalled, Scripted, unreachable
from intelligence.tests.record_fixtures import base_record

BASE = "http://backend.test"
ENGINES = {ENV_TESSERACT_CMD: "tesseract-test", ENV_PDFTOTEXT_CMD: "pdftotext-test"}
PAGE = "data.page_001"
PAGE_KEY = "page_001"

FLOOD = ["மேலூர்", "அருகே", "கருங்காலக்குடி", "கிராமத்தில்", "கால்வாய்", "உடைந்து", "வெள்ளம்", "புகுந்தது"]
OFFICIAL = ["வட்டாட்சியர்", "ராமசாமி", "நிவாரண", "முகாம்", "திறந்தார்"]
BREACH = "கால்வாய் உடைந்து வெள்ளம் புகுந்தது"
FLOODED = "வெள்ளம் புகுந்தது"
VILLAGE = "கருங்காலக்குடி கிராமத்தில்"
NAMED = "வட்டாட்சியர் ராமசாமி"


def tsv(*pages: Sequence[Sequence[tuple[str, float]]]) -> str:
    rows = ["\t".join(TSV_COLUMNS)]
    for number, lines in enumerate(pages, start=1):
        rows.append(f"1\t{number}\t0\t0\t0\t0\t0\t0\t2480\t3508\t-1\t")
        for row, words in enumerate(lines, start=1):
            for position, (word, confidence) in enumerate(words, start=1):
                rows.append(
                    f"5\t{number}\t1\t1\t{row}\t{position}\t{position * 90}\t{row * 60}\t80\t40\t"
                    f"{confidence}\t{word}"
                )
    return "\n".join(rows) + "\n"


def scored(words: Sequence[str], confidence: float = 91.0, **low: float) -> list[tuple[str, float]]:
    return [(word, low.get(f"w{index}", confidence)) for index, word in enumerate(words)]


NOTICE_TSV = tsv([scored(FLOOD), scored(OFFICIAL, w1=41.0)])


def engines(
    *,
    ocr: Optional[str] = None,
    pdf: Optional[str] = None,
    packs: Sequence[str] = ("eng", "osd", "tam"),
) -> Any:
    calls: list[list[str]] = []

    def run(args: Sequence[str]) -> tuple[int, bytes, bytes]:
        calls.append(list(args))
        if "--list-langs" in args:
            listed = 'List of available languages in "tessdata/" (3):\n' + "\n".join(packs) + "\n"
            return 0, listed.encode("utf-8"), b""
        if "--version" in args:
            return 0, b"tesseract v5.5.3.test\n leptonica-1.85.0\n", b""
        if args[-1] == "tsv":
            assert ocr is not None
            return 0, ocr.encode("utf-8"), b""
        if args[-1] == "-v":
            return 0, b"", b"pdftotext version 4.00\n"
        if args[-1] == "-":
            assert pdf is not None
            return 0, pdf.encode("utf-8"), b""
        raise AssertionError(f"unexpected engine call {args}")

    run.calls = calls
    return run


def scan(tmp_path: Path, name: str = "notice.png", content: bytes = b"scanned-notice") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def cite(source: str, words: str, field: str = PAGE, **extra: Any) -> dict[str, Any]:
    return {
        "value": words,
        "field": field,
        "quote": words,
        "char_start": source.index(words),
        "confidence": 0.85,
    } | extra


def flood_answer(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "record_kind": "incident",
        "description": cite(text, BREACH),
        "incident_type": cite(text, FLOODED, value="flood"),
        "event_status": cite(text, FLOODED, value="reported"),
        "locations": [
            cite(text, VILLAGE, text="கருங்காலக்குடி", normalized_name="Karungalakudi",
                 location_type="village", role="event_location")
        ],
    } | extra


def official(text: str) -> dict[str, Any]:
    return cite(text, NAMED, text="ராமசாமி", entity_type="government_official",
                role="responding_authority")


def notice(tmp_path: Path, ocr: str = NOTICE_TSV, **kwargs: Any) -> dict[str, Any]:
    reading = read_document(str(scan(tmp_path)), environ=ENGINES, runner=engines(ocr=ocr), **kwargs)
    return document_record(reading)


def claim(incident: Any, name: str) -> Any:
    return next(item for item in incident.claims if item.field == name)


def cited(incident: Any, item: Any) -> Any:
    return next(evidence for evidence in incident.evidence if evidence.evidence_id in item.evidence_ids)


def codes(incident: Any, field: Optional[str] = None) -> list[IssueCode]:
    return [
        issue.code for issue in incident.validation.issues if field is None or issue.field == field
    ]


def test_tsv_words_keep_their_offsets_lines_boxes_and_low_confidence():
    (page,) = parse_tsv(NOTICE_TSV)

    assert page.field == PAGE and page.number == 1
    assert page.text == " ".join(FLOOD) + "\n" + " ".join(OFFICIAL)
    (doubt,) = page.uncertain
    assert page.text[doubt.char_start:doubt.char_end] == "ராமசாமி" == doubt.text
    assert doubt.confidence == 41.0 and doubt.box == (180, 120, 80, 40)
    assert page.mean_confidence == pytest.approx((91.0 * 12 + 41.0) / 13, abs=0.1)
    assert page.low_quality is None and page.unreadable is None


def test_paragraphs_and_pages_stay_apart():
    two = "\t".join(TSV_COLUMNS) + "\n" + "\n".join([
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95\tone",
        "5\t1\t1\t2\t1\t1\t0\t0\t10\t10\t95\ttwo",
        "5\t2\t1\t1\t1\t1\t0\t0\t10\t10\t95\tthree",
    ])

    first, second = parse_tsv(two)

    assert first.text == "one\n\ntwo" and second.text == "three"
    assert (first.field, second.field) == ("data.page_001", "data.page_002")


def test_a_tamil_scan_goes_through_the_same_extraction_and_cites_its_page(tmp_path):
    record = notice(tmp_path)
    text = record["data"][PAGE_KEY]

    incident = extract_incident(record, provider=Scripted(flood_answer(text)))

    assert incident.incident_type.value == "flood"
    assert claim(incident, "incident_type").review is ReviewState.ACCEPTED
    evidence = cited(incident, claim(incident, "incident_type"))
    assert evidence.field == PAGE and evidence.quote == FLOODED
    assert evidence.modality is Modality.SCANNED_DOCUMENT
    assert evidence.method is ExtractionMethod.LLM and not evidence.is_source_fact
    assert "OCR text recognised by tesseract v5.5.3.test (tam+eng) from page 1 of notice.png" in (
        evidence.notes or ""
    )
    assert "not verified against the original" in evidence.notes
    assert incident.locations[0].review is ReviewState.ACCEPTED
    assert IssueCode.UNCERTAIN_RECOGNITION not in codes(incident)

    provenance = incident.provenance
    assert provenance.modality is Modality.SCANNED_DOCUMENT
    assert provenance.source_type == "document" and provenance.record_type == "image"
    assert provenance.raw_reference == str((tmp_path / "notice.png").resolve())
    assert provenance.language_hint == "ta+en"
    assert incident.incident_id == f"INC-{record['record_id']}"
    assert record["document"]["sha256"] == __import__("hashlib").sha256(b"scanned-notice").hexdigest()


def test_an_uncertain_name_keeps_its_evidence_but_is_not_accepted(tmp_path):
    record = notice(tmp_path)
    text = record["data"][PAGE_KEY]

    incident = extract_incident(record, provider=Scripted(flood_answer(text, entities=[official(text)])))

    (entity,) = incident.entities
    assert entity.review is ReviewState.REVIEW_REQUIRED
    assert cited(incident, entity).quote == NAMED
    assert codes(incident, "entities[ent-1]") == [IssueCode.UNCERTAIN_RECOGNITION]
    detail = next(issue.detail for issue in incident.validation.issues if issue.field == "entities[ent-1]")
    assert "'ராமசாமி' (41)" in detail and "notice.png page 1" in detail
    assert incident.review_required
    assert claim(incident, "incident_type").review is ReviewState.ACCEPTED


def test_an_uncertain_date_is_not_placed_on_the_clock(tmp_path):
    dated = tsv([scored(FLOOD), scored(["அக்டோபர்", "12", "அன்று"], w1=38.0)])
    record = notice(tmp_path, ocr=dated)
    text = record["data"][PAGE_KEY]
    answer = flood_answer(text, event_time=cite(text, "அக்டோபர் 12", value="2026-10-12",
                                                precision="day"))

    incident = extract_incident(record, provider=Scripted(answer))

    assert incident.event_time is None
    assert claim(incident, "event_time").review is ReviewState.REVIEW_REQUIRED
    assert IssueCode.UNCERTAIN_RECOGNITION in codes(incident, "event_time")


def test_a_low_quality_page_sends_every_span_on_it_to_review(tmp_path):
    record = notice(tmp_path, ocr=tsv([scored(FLOOD, 64.0), scored(OFFICIAL, 63.0)]))
    text = record["data"][PAGE_KEY]
    assert record["document"]["pages"][0]["uncertain"] == []

    incident = extract_incident(record, provider=Scripted(flood_answer(text)))

    assert incident.description is None and incident.incident_type.value == "unresolved"
    assert claim(incident, "description").review is ReviewState.REVIEW_REQUIRED
    assert IssueCode.UNCERTAIN_RECOGNITION in codes(incident, "description")
    assert incident.locations[0].review is ReviewState.REVIEW_REQUIRED
    assert any("notice.png page 1: the page was recognised with a mean word confidence of 63.6"
               in warning for warning in incident.generation.warnings)


def test_a_missing_tamil_pack_is_refused_rather_than_read_with_english(tmp_path):
    runner = engines(ocr=NOTICE_TSV, packs=("eng", "osd"))

    with pytest.raises(DocumentError, match=r"no tam language pack \(tam.traineddata\)"):
        read_document(str(scan(tmp_path)), environ=ENGINES, runner=runner)

    assert not any(call[-1] == "tsv" for call in runner.calls)


def test_english_only_and_bilingual_documents_name_their_packs(tmp_path):
    english = tsv([scored(["Canal", "breach", "flooded", "Melur"])])
    runner = engines(ocr=english, packs=("eng",))

    reading = read_document(str(scan(tmp_path)), languages=("en",), environ=ENGINES, runner=runner)

    assert reading.pages[0].text == "Canal breach flooded Melur"
    assert ["-l", "eng"] == next(call for call in runner.calls if call[-1] == "tsv")[3:5]
    bilingual = engines(ocr=NOTICE_TSV)
    read_document(str(scan(tmp_path)), environ=ENGINES, runner=bilingual)
    assert "tam+eng" in next(call for call in bilingual.calls if call[-1] == "tsv")


def test_a_pdf_text_layer_cites_its_page_and_reports_the_page_it_could_not_read(tmp_path):
    layer = "Canal breach near Melur.\f\fCollector visited the site.\f"
    path = scan(tmp_path, "notice.pdf", b"%PDF-1.4 notice")
    record = document_record(read_document(str(path), environ=ENGINES, runner=engines(pdf=layer)))
    text = record["data"]["page_003"]
    answer = {"record_kind": "incident",
              "description": cite(text, "Collector visited the site.", field="data.page_003")}

    incident = extract_incident(record, provider=Scripted(answer))

    assert record["record_type"] == "pdf" and record["data"]["page_002"] == ""
    assert incident.provenance.modality is Modality.PDF
    assert incident.provenance.fields_without_value == ["data.page_002"]
    evidence = cited(incident, claim(incident, "description"))
    assert evidence.field == "data.page_003" and evidence.modality is Modality.PDF
    assert "text layer of page 3 of notice.pdf, extracted by pdftotext version 4.00" in evidence.notes
    assert claim(incident, "description").review is ReviewState.ACCEPTED
    assert any("notice.pdf page 2: this page has no text layer" in warning
               for warning in incident.generation.warnings)


def test_a_scanned_pdf_with_no_text_layer_is_refused_not_guessed(tmp_path):
    path = scan(tmp_path, "scan.pdf", b"%PDF-1.4 scan")

    with pytest.raises(DocumentError, match="does not rasterise a PDF"):
        read_document(str(path), environ=ENGINES, runner=engines(pdf="\f\f"))


def test_a_tamil_text_layer_without_tamil_characters_is_flagged(tmp_path):
    legacy = "jkpH;ehL fhy;tha; cile;jJ\f"
    path = scan(tmp_path, "legacy.pdf", b"%PDF-1.4 legacy")
    record = document_record(
        read_document(str(path), languages=("ta",), environ=ENGINES, runner=engines(pdf=legacy))
    )
    text = record["data"]["page_001"]

    incident = extract_incident(
        record, provider=Scripted({"record_kind": "incident", "description": cite(text, text)})
    )

    assert "legacy Tamil font encoding" in record["document"]["pages"][0]["low_quality"]
    assert claim(incident, "description").review is ReviewState.REVIEW_REQUIRED
    assert IssueCode.UNCERTAIN_RECOGNITION in codes(incident, "description")


@pytest.mark.parametrize(
    "output, message",
    [
        ("level\ttext\n5\tword\n", "column header"),
        ("\t".join(TSV_COLUMNS) + "\n5\t1\t1\t1\t1\t1\t0\t0\t10\n", "has 9 columns"),
        ("\t".join(TSV_COLUMNS) + "\n5\t1\t1\t1\t1\t1\t0\t0\t10\t10\thigh\tword\n", "malformed"),
        ("", "column header"),
    ],
)
def test_malformed_tesseract_output_is_refused(output, message):
    with pytest.raises(DocumentError, match=message):
        parse_tsv(output)


def test_an_image_with_no_recognised_words_is_refused(tmp_path):
    with pytest.raises(DocumentError, match="no text was recognised in notice.png"):
        read_document(str(scan(tmp_path)), environ=ENGINES, runner=engines(ocr=tsv([])))


def test_unsupported_empty_and_missing_files_are_refused(tmp_path):
    runner = engines()
    with pytest.raises(DocumentError, match=r"\.docx is not a document this layer reads"):
        read_document(str(scan(tmp_path, "report.docx")), environ=ENGINES, runner=runner)
    with pytest.raises(DocumentError, match="is empty"):
        read_document(str(scan(tmp_path, "blank.png", b"")), environ=ENGINES, runner=runner)
    with pytest.raises(DocumentError, match="is not a file"):
        read_document(str(tmp_path / "absent.png"), environ=ENGINES, runner=runner)
    assert runner.calls == []


def test_no_tesseract_anywhere_names_the_setting_to_fill(tmp_path, monkeypatch):
    monkeypatch.setattr(documents.shutil, "which", lambda name: None)
    monkeypatch.setattr(documents, "WINDOWS_TESSERACT", str(tmp_path / "absent.exe"))

    with pytest.raises(DocumentError, match=ENV_TESSERACT_CMD):
        read_document(str(scan(tmp_path)), environ={}, runner=engines())


def test_an_engine_that_cannot_start_is_a_document_error(tmp_path):
    def broken(args: Sequence[str]) -> tuple[int, bytes, bytes]:
        raise FileNotFoundError(2, "No such file", args[0])

    with pytest.raises(DocumentError, match="could not be started from 'tesseract-test'"):
        read_document(str(scan(tmp_path)), environ=ENGINES, runner=broken)


def test_a_malformed_document_block_makes_the_record_unreadable(tmp_path):
    record = notice(tmp_path)

    with pytest.raises(RecordShapeError, match="must be a mapping"):
        build_context(record | {"document": "page one"})
    past = json.loads(json.dumps(record))
    past["document"]["pages"][0]["uncertain"] = [{"char_start": 0, "char_end": 10_000, "text": "x"}]
    with pytest.raises(RecordShapeError, match="past the end of its own text"):
        build_context(past)
    guessed = json.loads(json.dumps(record))
    guessed["document"]["pages"][0]["method"] = "guess"
    with pytest.raises(RecordShapeError, match="malformed"):
        build_context(guessed)


def test_only_a_document_request_carries_the_misreading_line(tmp_path):
    news = extraction_request(build_context(base_record()))
    scanned = extraction_request(build_context(notice(tmp_path)))

    assert "misreadings included" not in news.user and "data.page_" not in news.user
    assert "misreadings included, never corrected" in scanned.user
    assert news.system.split("The record's own metadata")[0] == scanned.system.split(
        "The record's own metadata"
    )[0]
    assert PROMPT_DIGEST == "6e2f43b839df9fca"
    assert "document" not in read_record(base_record()).as_dict()


def test_changed_bytes_or_engine_change_the_record_and_its_hash(tmp_path):
    first = notice(tmp_path)
    second = document_record(read_document(
        str(scan(tmp_path, "notice.png", b"another-scan")), environ=ENGINES,
        runner=engines(ocr=NOTICE_TSV),
    ))

    assert first["record_id"] != second["record_id"]
    reread = json.loads(json.dumps(first))
    reread["document"]["engine"] = "tesseract v6.0.0 (tam+eng)"
    assert fingerprint(reread) != fingerprint(first) is not None


def run_cli(tmp_path: Path, args: list[str], provider: Any, runner: Any, **kwargs: Any) -> int:
    return main(
        args,
        provider=provider,
        environ=ENGINES | {ENV_API_BASE_URL: BASE},
        transport=unreachable(AssertionError("a document run never reads GET /records")),
        document_runner=runner,
        **kwargs,
    )


def test_a_document_run_checkpoints_and_reuses_without_asking_again(tmp_path, capsys):
    path = scan(tmp_path)
    text = parse_tsv(NOTICE_TSV)[0].text
    state = str(tmp_path / "run")

    first = run_cli(tmp_path, ["--document", str(path), "--state-dir", state],
                    Scripted(flood_answer(text)), engines(ocr=NOTICE_TSV))
    capsys.readouterr()
    again = run_cli(tmp_path, ["--document", str(path), "--state-dir", state],
                    NeverCalled(), engines(ocr=NOTICE_TSV))

    assert first == EXIT_OK and again == EXIT_OK
    assert "1 selected = 1 reused + 0 attempted" in capsys.readouterr().out


def test_the_same_bytes_given_twice_are_read_once(tmp_path, capsys):
    one = scan(tmp_path, "a.png")
    two = scan(tmp_path, "b.png")
    text = parse_tsv(NOTICE_TSV)[0].text

    code = run_cli(tmp_path, ["--document", str(one), "--document", str(two)],
                   Scripted(flood_answer(text)), engines(ocr=NOTICE_TSV))

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "the same bytes as an earlier --document, read once as DOC-" in out


def test_an_uncertain_document_reading_is_never_posted(tmp_path, capsys):
    text = parse_tsv(NOTICE_TSV)[0].text
    posted: list[Any] = []

    def post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
        posted.append(body)
        raise AssertionError("a review-required reading must not be posted")

    code = run_cli(tmp_path, ["--document", str(scan(tmp_path)), "--post-incidents"],
                   Scripted(flood_answer(text, entities=[official(text)])),
                   engines(ocr=NOTICE_TSV), incident_transport=post)

    assert code == EXIT_OK and posted == []
    assert "POST /incidents: nothing to send" in capsys.readouterr().out


def test_a_document_that_cannot_be_read_stops_before_any_model_call(tmp_path, capsys):
    code = run_cli(tmp_path, ["--document", str(scan(tmp_path))], NeverCalled(),
                   engines(ocr=NOTICE_TSV, packs=("eng",)))

    assert code == EXIT_DOCUMENT
    assert "document failed: Tesseract has no tam language pack" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--document", "a.png", "--limit", "2"],
        ["--document", "a.png", "--record-id", "R1"],
        ["--document-language", "en"],
        ["--source-url", "https://example.test/a"],
        ["--document", "a.png", "--document", "b.png", "--source-url", "https://example.test/a"],
        ["--document", "a.png", "--document-language", "hi"],
    ],
)
def test_document_options_that_contradict_each_other_are_rejected(args):
    with pytest.raises(SystemExit) as stopped:
        main(args, provider=NeverCalled(), environ={ENV_API_BASE_URL: BASE})
    assert stopped.value.code == 2


def test_a_source_url_is_kept_as_the_document_s_source_reference(tmp_path):
    reading = read_document(str(scan(tmp_path)), environ=ENGINES, runner=engines(ocr=NOTICE_TSV))
    record = document_record(reading, source_url="https://collector.example.test/notice-12.png")
    text = record["data"][PAGE_KEY]

    incident = extract_incident(record, provider=Scripted(flood_answer(text)))

    assert incident.provenance.source_url == "https://collector.example.test/notice-12.png"
    assert cited(incident, claim(incident, "description")).source_url == record["source_url"]
