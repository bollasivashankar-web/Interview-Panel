import json
import os
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore

from backend.pdf_questions import MAX_PDF_BYTES, PdfQuestionError


slots = BoundedSemaphore(2)


class PdfBusyError(PdfQuestionError):
    pass


def process_pdf(data: bytes) -> list[str]:
    if len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF-"):
        raise PdfQuestionError("Upload a valid PDF no larger than 5 MB")
    if not slots.acquire(blocking=False):
        raise PdfBusyError("Document processing is busy; retry shortly")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.pdf_worker"], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12,
            cwd=Path(__file__).resolve().parent.parent, check=False,
            env={**{name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if name in os.environ},
                 "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            raise PdfQuestionError("PDF could not be processed within the document limits")
        try:
            questions = json.loads(result.stdout)["questions"]
            if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
                raise ValueError("Invalid parser result")
        except (ValueError, KeyError, TypeError) as exc:
            raise PdfQuestionError("PDF could not be processed") from exc
        return questions
    except subprocess.TimeoutExpired as exc:
        raise PdfQuestionError("PDF processing exceeded the time limit") from exc
    finally:
        slots.release()
