from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from intelligence.llm import load_dotenv_file
from intelligence.models.enums import Modality
from intelligence.records import RecordShapeError

ENV_TESSERACT_CMD = "INTELLIGENCE_TESSERACT_CMD"
ENV_PDFTOTEXT_CMD = "INTELLIGENCE_PDFTOTEXT_CMD"
WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DOCUMENT_KEY = "document"
DOCUMENT_SOURCE_ID = "local-document"
DOCUMENT_SOURCE_TYPE = "document"
PAGE_PREFIX = "data.page_"

OCR = "ocr"
TEXT_LAYER = "text_layer"

WORD_FLOOR = 60.0
PAGE_FLOOR = 70.0
RUN_TIMEOUT_SECONDS = 300

LANGUAGE_PACKS = {"ta": "tam", "en": "eng"}
LANGUAGE_CHOICES = ("ta+en", "ta", "en")
DEFAULT_LANGUAGES = "ta+en"

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}
PDF_TYPE = "application/pdf"

TSV_COLUMNS = (
    "level", "page_num", "block_num", "par_num", "line_num", "word_num",
    "left", "top", "width", "height", "conf", "text",
)
WORD_LEVEL = 5

Runner = Callable[[Sequence[str]], tuple[int, bytes, bytes]]


class DocumentError(RuntimeError):
    ...


@dataclass(frozen=True)
class Uncertain:
    char_start: int
    char_end: int
    text: str
    confidence: Optional[float] = None
    box: Optional[tuple[int, int, int, int]] = None

    def as_block(self) -> dict[str, Any]:
        return {
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": self.text,
            "confidence": self.confidence,
            "box": None if self.box is None else list(self.box),
        }

    def describe(self) -> str:
        level = "unscored" if self.confidence is None else f"{self.confidence:g}"
        return f"{self.text!r} ({level})"


@dataclass(frozen=True)
class Page:
    field: str
    number: int
    method: str
    text: str = ""
    mean_confidence: Optional[float] = None
    low_quality: Optional[str] = None
    unreadable: Optional[str] = None
    uncertain: tuple[Uncertain, ...] = ()

    @property
    def modality(self) -> Modality:
        return Modality.SCANNED_DOCUMENT if self.method == OCR else Modality.PDF

    def doubts(self, start: int, end: int) -> list[Uncertain]:
        return [item for item in self.uncertain if item.char_start < end and start < item.char_end]

    def as_block(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "page": self.number,
            "method": self.method,
            "mean_confidence": self.mean_confidence,
            "low_quality": self.low_quality,
            "unreadable": self.unreadable,
            "uncertain": [item.as_block() for item in self.uncertain],
        }


@dataclass(frozen=True)
class Document:
    file_name: str
    sha256: str
    media_type: str
    record_type: str
    engine: str
    languages: tuple[str, ...]
    pages: tuple[Page, ...]
    path: str = ""
    modified_at: Optional[datetime] = None

    @property
    def modality(self) -> Modality:
        if any(page.method == OCR for page in self.pages):
            return Modality.SCANNED_DOCUMENT
        return Modality.PDF

    @property
    def record_id(self) -> str:
        return f"DOC-{self.sha256[:16]}"

    def page(self, field: str) -> Optional[Page]:
        return next((page for page in self.pages if page.field == field), None)

    def note(self, page: Page) -> str:
        where = f"page {page.number} of {self.file_name}"
        if page.method == OCR:
            level = "unscored" if page.mean_confidence is None else f"{page.mean_confidence:g}"
            return (
                f"OCR text recognised by {self.engine} from {where} (page mean confidence {level}); "
                "recognised, not verified against the original"
            )
        return (
            f"text layer of {where}, extracted by {self.engine}; the PDF's own text, not verified "
            "against the printed page"
        )

    def doubt(self, page: Page, start: int, end: int) -> Optional[str]:
        where = f"{self.file_name} page {page.number}"
        if page.low_quality:
            return (
                f"{where}: {page.low_quality}, so every span on it waits for a person to read it "
                "against the original"
            )
        words = page.doubts(start, end)
        if not words:
            return None
        return (
            f"{where}: the span holds words recognised below confidence {WORD_FLOOR:g} - "
            + ", ".join(item.describe() for item in words)
            + " - so it waits for a person to read it against the original"
        )

    def as_block(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "record_type": self.record_type,
            "modality": self.modality.value,
            "engine": self.engine,
            "languages": list(self.languages),
            "word_floor": WORD_FLOOR,
            "page_floor": PAGE_FLOOR,
            "pages": [page.as_block() for page in self.pages],
        }

    @classmethod
    def from_block(cls, block: Any) -> "Document":
        if not isinstance(block, Mapping):
            raise RecordShapeError(f"the document block must be a mapping, got {type(block).__name__}")
        try:
            pages = tuple(_page_from_block(item) for item in block["pages"])
            return cls(
                file_name=str(block["file_name"]),
                sha256=str(block["sha256"]),
                media_type=str(block["media_type"]),
                record_type=str(block["record_type"]),
                engine=str(block["engine"]),
                languages=tuple(str(code) for code in block["languages"]),
                pages=pages,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RecordShapeError(f"the document block is malformed: {error!r}") from error


def _page_from_block(item: Mapping[str, Any]) -> Page:
    method = str(item["method"])
    if method not in (OCR, TEXT_LAYER):
        raise ValueError(f"page method {method!r} is neither {OCR} nor {TEXT_LAYER}")
    uncertain = []
    for entry in item.get("uncertain") or ():
        start, end = int(entry["char_start"]), int(entry["char_end"])
        if not 0 <= start <= end:
            raise ValueError(f"uncertain range {start}:{end} is not a span")
        box = entry.get("box")
        uncertain.append(
            Uncertain(
                char_start=start,
                char_end=end,
                text=str(entry["text"]),
                confidence=None if entry.get("confidence") is None else float(entry["confidence"]),
                box=None if box is None else tuple(int(value) for value in box),
            )
        )
    mean = item.get("mean_confidence")
    return Page(
        field=str(item["field"]),
        number=int(item["page"]),
        method=method,
        mean_confidence=None if mean is None else float(mean),
        low_quality=item.get("low_quality") or None,
        unreadable=item.get("unreadable") or None,
        uncertain=tuple(uncertain),
    )


def parse_languages(choice: str) -> tuple[str, ...]:
    codes = tuple(code.strip() for code in str(choice).split("+") if code.strip())
    unknown = [code for code in codes if code not in LANGUAGE_PACKS]
    if not codes or unknown:
        raise DocumentError(
            f"{choice!r} names no language this layer reads; use one of {', '.join(LANGUAGE_CHOICES)}"
        )
    return codes


def _run(args: Sequence[str]) -> tuple[int, bytes, bytes]:
    done = subprocess.run(list(args), capture_output=True, timeout=RUN_TIMEOUT_SECONDS, check=False)
    return done.returncode, done.stdout, done.stderr


def _call(runner: Runner, args: Sequence[str], *, what: str, check: bool = True) -> tuple[bytes, bytes]:
    try:
        code, out, err = runner(args)
    except subprocess.TimeoutExpired as error:
        raise DocumentError(f"{what} did not finish within {RUN_TIMEOUT_SECONDS}s") from error
    except OSError as error:
        raise DocumentError(f"{what} could not be started from {args[0]!r}: {error}") from error
    if check and code != 0:
        detail = err.decode("utf-8", "replace").strip()[:300]
        raise DocumentError(f"{what} exited with {code}: {detail or 'no message'}")
    return out, err


def _decode(raw: bytes, *, what: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentError(f"{what} printed text that is not UTF-8: {error}") from error


def engine_path(
    name: str,
    env_key: str,
    environ: Mapping[str, str],
    fallbacks: Sequence[str] = (),
) -> Optional[str]:
    configured = (environ.get(env_key) or "").strip()
    if configured:
        return configured
    found = shutil.which(name)
    if found:
        return found
    return next((path for path in fallbacks if os.path.isfile(path)), None)


def _version(runner: Runner, args: Sequence[str], *, what: str) -> str:
    out, err = _call(runner, args, what=what, check=False)
    text = (out + b"\n" + err).decode("utf-8", "replace")
    return next((line.strip() for line in text.splitlines() if line.strip()), what)


def installed_packs(runner: Runner, command: str) -> tuple[str, ...]:
    out, _ = _call(runner, [command, "--list-langs"], what="tesseract --list-langs")
    lines = _decode(out, what="tesseract --list-langs").splitlines()
    return tuple(line.strip() for line in lines if line.strip() and not line.startswith("List of"))


def parse_tsv(text: str) -> tuple[Page, ...]:
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if not lines or tuple(lines[0].split("\t")) != TSV_COLUMNS:
        raise DocumentError("Tesseract's TSV output does not start with its column header")
    words: dict[int, list[tuple[tuple[int, int, int], tuple[int, int, int, int], float, str]]] = {}
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(TSV_COLUMNS):
            raise DocumentError(
                f"TSV line {number} has {len(cells)} columns where Tesseract prints {len(TSV_COLUMNS)}"
            )
        try:
            level, page, block, paragraph, row = (int(cell) for cell in cells[:5])
            box = tuple(int(cell) for cell in cells[6:10])
            confidence = round(float(cells[10]), 1)
        except ValueError as error:
            raise DocumentError(f"TSV line {number} is malformed: {error}") from error
        held = words.setdefault(page, [])
        if level == WORD_LEVEL and cells[11].strip():
            held.append(((block, paragraph, row), box, confidence, cells[11].strip()))
    if not words:
        raise DocumentError("Tesseract's TSV output holds no page")
    width = max(3, len(str(max(words))))
    return tuple(_ocr_page(number, words[number], width) for number in sorted(words))


def _ocr_page(number: int, words: Sequence[Any], width: int) -> Page:
    parts: list[str] = []
    uncertain: list[Uncertain] = []
    scored: list[float] = []
    position = 0
    previous: Optional[tuple[int, int, int]] = None
    for key, box, confidence, word in words:
        if previous is not None:
            gap = " " if key == previous else ("\n" if key[:2] == previous[:2] else "\n\n")
            parts.append(gap)
            position += len(gap)
        start = position
        parts.append(word)
        position += len(word)
        known = confidence >= 0
        if known:
            scored.append(confidence)
        if not known or confidence < WORD_FLOOR:
            uncertain.append(
                Uncertain(start, position, word, confidence if known else None, box)
            )
        previous = key
    text = "".join(parts)
    mean = round(sum(scored) / len(scored), 1) if scored else None
    low_quality = None
    unreadable = None
    if not text:
        unreadable = "no text was recognised on this page"
    elif mean is None or mean < PAGE_FLOOR:
        level = "no scored word" if mean is None else f"a mean word confidence of {mean:g}"
        low_quality = f"the page was recognised with {level}, below {PAGE_FLOOR:g}"
    return Page(
        field=f"{PAGE_PREFIX}{number:0{width}d}",
        number=number,
        method=OCR,
        text=text,
        mean_confidence=mean,
        low_quality=low_quality,
        unreadable=unreadable,
        uncertain=tuple(uncertain),
    )


def _has_tamil(text: str) -> bool:
    return any("\u0b80" <= character <= "\u0bff" for character in text)


def _text_layer_pages(text: str, languages: Sequence[str]) -> tuple[Page, ...]:
    chunks = text.split("\f")
    if text.endswith("\f"):
        chunks = chunks[:-1]
    width = max(3, len(str(len(chunks))))
    pages = []
    for number, chunk in enumerate(chunks, start=1):
        body = chunk.strip()
        low_quality = None
        unreadable = None
        if not body:
            unreadable = "this page has no text layer, and this layer does not rasterise a PDF to OCR it"
        elif tuple(languages) == ("ta",) and not _has_tamil(body):
            low_quality = (
                "the document was declared Tamil but this page's text layer holds no Tamil "
                "character, which is how a legacy Tamil font encoding prints"
            )
        pages.append(
            Page(
                field=f"{PAGE_PREFIX}{number:0{width}d}",
                number=number,
                method=TEXT_LAYER,
                text=body,
                low_quality=low_quality,
                unreadable=unreadable,
            )
        )
    return tuple(pages)


def read_document(
    path: str,
    *,
    languages: Sequence[str] = ("ta", "en"),
    environ: Optional[Mapping[str, str]] = None,
    runner: Optional[Runner] = None,
) -> Document:
    if environ is None:
        load_dotenv_file()
        environ = os.environ
    run = runner or _run
    file = Path(path)
    if not file.is_file():
        raise DocumentError(f"{path} is not a file")
    suffix = file.suffix.casefold()
    if suffix != ".pdf" and suffix not in IMAGE_TYPES:
        raise DocumentError(
            f"{file.name}: {suffix or 'a file with no suffix'} is not a document this layer reads; "
            f"it reads PDFs and images ({', '.join(sorted(IMAGE_TYPES))})"
        )
    content = file.read_bytes()
    if not content:
        raise DocumentError(f"{file.name} is empty")
    unknown = [code for code in languages if code not in LANGUAGE_PACKS]
    if not languages or unknown:
        raise DocumentError(f"no OCR language for {', '.join(unknown) or 'an empty list'}")

    if suffix == ".pdf":
        command = engine_path("pdftotext", ENV_PDFTOTEXT_CMD, environ)
        if command is None:
            raise DocumentError(
                f"no pdftotext was found: put it on PATH or set {ENV_PDFTOTEXT_CMD} to its path"
            )
        engine = _version(run, [command, "-v"], what="pdftotext")
        out, _ = _call(run, [command, "-enc", "UTF-8", str(file), "-"], what="pdftotext")
        pages = _text_layer_pages(_decode(out, what="pdftotext"), languages)
        media_type, record_type = PDF_TYPE, "pdf"
        empty = (
            f"no page of {file.name} has a text layer and this layer does not rasterise a PDF to "
            "OCR it; export its pages as images and pass each one as a document"
        )
    else:
        command = engine_path("tesseract", ENV_TESSERACT_CMD, environ, (WINDOWS_TESSERACT,))
        if command is None:
            raise DocumentError(
                f"no Tesseract was found: put tesseract on PATH or set {ENV_TESSERACT_CMD} to its path"
            )
        packs = [LANGUAGE_PACKS[code] for code in languages]
        missing = [pack for pack in packs if pack not in installed_packs(run, command)]
        if missing:
            raise DocumentError(
                f"Tesseract has no {', '.join(missing)} language pack "
                f"({', '.join(pack + '.traineddata' for pack in missing)}); install it into its "
                "tessdata folder, or name only the languages the document is written in. Text is "
                "never recognised with a model for another script"
            )
        engine = f"{_version(run, [command, '--version'], what='tesseract')} ({'+'.join(packs)})"
        out, _ = _call(run, [command, str(file), "stdout", "-l", "+".join(packs), "tsv"], what="tesseract")
        pages = parse_tsv(_decode(out, what="tesseract"))
        media_type, record_type = IMAGE_TYPES[suffix], "image"
        empty = f"no text was recognised in {file.name}"

    if not any(page.text for page in pages):
        raise DocumentError(empty)
    return Document(
        file_name=file.name,
        sha256=hashlib.sha256(content).hexdigest(),
        media_type=media_type,
        record_type=record_type,
        engine=engine,
        languages=tuple(languages),
        pages=pages,
        path=str(file.resolve()),
        modified_at=datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc),
    )


def document_record(document: Document, *, source_url: Optional[str] = None) -> dict[str, Any]:
    data: dict[str, Any] = {page.field.removeprefix("data."): page.text for page in document.pages}
    data["language"] = "+".join(document.languages)
    return {
        "record_id": document.record_id,
        "source_id": DOCUMENT_SOURCE_ID,
        "source_type": DOCUMENT_SOURCE_TYPE,
        "record_type": document.record_type,
        "event_time": None,
        "location": None,
        "data": data,
        "severity": None,
        "status": None,
        "source_url": source_url,
        "raw_reference": document.path,
        "retrieved_at": document.modified_at.isoformat(),
        DOCUMENT_KEY: json.loads(json.dumps(document.as_block(), ensure_ascii=False)),
    }


__all__ = [
    "DEFAULT_LANGUAGES",
    "DOCUMENT_KEY",
    "Document",
    "DocumentError",
    "ENV_PDFTOTEXT_CMD",
    "ENV_TESSERACT_CMD",
    "LANGUAGE_CHOICES",
    "PAGE_FLOOR",
    "Page",
    "Uncertain",
    "WORD_FLOOR",
    "document_record",
    "parse_languages",
    "parse_tsv",
    "read_document",
]
