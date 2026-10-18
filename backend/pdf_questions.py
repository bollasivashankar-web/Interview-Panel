import re
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError


MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_EXTRACTED_QUESTIONS = 500
_QUESTION_START = re.compile(r"^\s*(?:(?:q(?:uestion)?\s*)?\d{1,3}[.)\-:]|[-•*])\s*(.+)$", re.IGNORECASE)


class PdfQuestionError(ValueError):
    pass


def questions_from_text(text: str) -> list[str]:
    questions: list[str] = []
    seen: set[str] = set()
    current = ""

    def add(value: str) -> None:
        cleaned = " ".join(value.split()).strip(" -•\t")
        normalized = cleaned.casefold()
        if 5 <= len(cleaned) <= 5_000 and normalized not in seen:
            questions.append(cleaned)
            seen.add(normalized)

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            if current:
                add(current)
                current = ""
            continue
        match = _QUESTION_START.match(line)
        if match:
            if current:
                add(current)
            current = match.group(1)
            if current.endswith("?"):
                add(current)
                current = ""
        elif line.endswith("?"):
            if current:
                current = f"{current} {line}"
                add(current)
                current = ""
            else:
                add(line)
        elif current:
            current = f"{current} {line}"
    if current:
        add(current)
    return questions[:MAX_EXTRACTED_QUESTIONS]


def extract_pdf_questions(data: bytes) -> list[str]:
    if not data.startswith(b"%PDF-"):
        raise PdfQuestionError("The selected file is not a valid PDF")
    if len(data) > MAX_PDF_BYTES:
        raise PdfQuestionError("PDF must be 5 MB or smaller")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise PdfQuestionError("Password-protected PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise PdfQuestionError(f"PDF must have no more than {MAX_PDF_PAGES} pages")
        pieces = []
        characters = 0
        for page in reader.pages:
            piece = page.extract_text() or ""
            characters += len(piece)
            if characters > 500_000:
                raise PdfQuestionError("PDF contains too much extracted text")
            pieces.append(piece)
        text = "\n\n".join(pieces)
    except PdfQuestionError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfQuestionError("The PDF could not be read") from exc

    questions = questions_from_text(text)
    if not questions:
        raise PdfQuestionError("No questions were found. Use a text-based PDF with numbered, bulleted, or question-mark-ended questions")
    return questions
