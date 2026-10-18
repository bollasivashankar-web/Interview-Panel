"""Short-lived PDF parser; receives bytes on stdin and emits a bounded JSON result."""
import json
import sys

from backend.pdf_questions import MAX_PDF_BYTES, PdfQuestionError, extract_pdf_questions


def main():
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        questions = extract_pdf_questions(sys.stdin.buffer.read(MAX_PDF_BYTES + 1))
        print(json.dumps({"questions": questions}))
    except (PdfQuestionError, MemoryError):
        print(json.dumps({"error": "PDF could not be processed within the document limits"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
