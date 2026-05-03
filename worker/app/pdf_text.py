from dataclasses import dataclass
from pathlib import Path
import re

import pdfplumber


@dataclass
class PDFTextExtractionResult:
    pdf_path: Path
    page_count: int
    pages_with_text: int
    total_chars: int
    avg_chars_per_page: float
    text: str
    quality_flags: list[str]

    @property
    def extraction_quality(self) -> str:
        if "empty_text" in self.quality_flags:
            return "failed"
        if "low_text_coverage" in self.quality_flags or "very_low_char_count" in self.quality_flags:
            return "poor"
        if self.quality_flags:
            return "needs_review"
        return "good"


class PDFTextExtractor:
    def __init__(self, min_chars_per_page: int = 500, min_text_page_ratio: float = 0.70) -> None:
        self.min_chars_per_page = min_chars_per_page
        self.min_text_page_ratio = min_text_page_ratio

    def extract(self, pdf_path: Path) -> PDFTextExtractionResult:
        pdf_path = Path(pdf_path)
        page_texts = []

        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)

            for page in pdf.pages:
                page_text = page.extract_text() or ""
                page_texts.append(self.clean_text(page_text))

        pages_with_text = sum(1 for page_text in page_texts if page_text.strip())
        text = "\n\n".join(page_text for page_text in page_texts if page_text.strip())
        total_chars = len(text)
        avg_chars_per_page = total_chars / page_count if page_count else 0

        quality_flags = self.evaluate_quality(
            page_count=page_count,
            pages_with_text=pages_with_text,
            total_chars=total_chars,
            avg_chars_per_page=avg_chars_per_page,
            text=text,
        )

        return PDFTextExtractionResult(
            pdf_path=pdf_path,
            page_count=page_count,
            pages_with_text=pages_with_text,
            total_chars=total_chars,
            avg_chars_per_page=avg_chars_per_page,
            text=text,
            quality_flags=quality_flags,
        )

    def save_text(self, result: PDFTextExtractionResult, output_dir: Path) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"{result.pdf_path.stem}.txt"
        output_file.write_text(result.text, encoding="utf-8")

        return output_file

    def evaluate_quality(
        self,
        page_count: int,
        pages_with_text: int,
        total_chars: int,
        avg_chars_per_page: float,
        text: str,
    ) -> list[str]:
        flags = []

        if total_chars == 0:
            flags.append("empty_text")
            return flags

        text_page_ratio = pages_with_text / page_count if page_count else 0

        if text_page_ratio < self.min_text_page_ratio:
            flags.append("low_text_coverage")

        if avg_chars_per_page < self.min_chars_per_page:
            flags.append("very_low_char_count")

        if self.has_excessive_garbage(text):
            flags.append("possible_ocr_or_encoding_garbage")

        if not self.has_contract_keywords(text):
            flags.append("missing_expected_contract_keywords")

        return flags

    @staticmethod
    def clean_text(text: str) -> str:
        text = text.replace("\x00", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def has_contract_keywords(text: str) -> bool:
        keywords = [
            "agreement",
            "grievance",
            "arbitration",
            "union",
            "employer",
            "employee",
            "bargaining",
        ]
        lower_text = text.lower()
        return any(keyword in lower_text for keyword in keywords)

    @staticmethod
    def has_excessive_garbage(text: str) -> bool:
        if not text:
            return True

        chars = len(text)
        weird_chars = len(re.findall(r"[�□■●◆]", text))
        alpha_chars = len(re.findall(r"[A-Za-z]", text))

        weird_ratio = weird_chars / chars
        alpha_ratio = alpha_chars / chars

        return weird_ratio > 0.01 or alpha_ratio < 0.35