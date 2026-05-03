from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class ContractSection:
    source_file: str
    article_number: str
    article_title: str
    start_char: int
    end_char: int
    text: str


class ContractSectionExtractor:
    ARTICLE_PATTERN = re.compile(
        r"(?im)^\s*ARTICLE\s+(?P<number>\d+[A-Z]?)\s+"
        r"(?P<title>[A-Z][A-Z0-9 ,/&()\-]{3,})\s*$"
    )

    def __init__(self, min_start_char: int = 8_000) -> None:
        self.min_start_char = min_start_char

    def extract_sections(self, text_path: Path) -> list[ContractSection]:
        text_path = Path(text_path)
        text = text_path.read_text(encoding="utf-8")

        headings = [
            match
            for match in self.ARTICLE_PATTERN.finditer(text)
            if match.start() >= self.min_start_char
        ]

        sections = []
        for idx, heading in enumerate(headings):
            start_char = heading.start()
            end_char = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)

            sections.append(
                ContractSection(
                    source_file=text_path.name,
                    article_number=heading.group("number").strip(),
                    article_title=self.clean_heading(heading.group("title")),
                    start_char=start_char,
                    end_char=end_char,
                    text=text[start_char:end_char].strip(),
                )
            )

        return sections

    @staticmethod
    def clean_heading(value: str) -> str:
        value = re.sub(r"\s+", " ", value)
        return value.strip(" .-")
    
    RELEVANCE_KEYWORDS = {
        "grievance_procedure": ["grievance", "complaint"],
        "arbitration": ["arbitration", "arbitrator"],
        "discipline": ["discipline", "suspension", "removal", "adverse action"],
        "performance": ["performance", "unacceptable performance"],
        "contract_term": ["duration", "expiration", "renewal", "reopener", "termination"],
        "notice": ["notice", "written notice", "days", "time limit", "deadline"],
    }

    def classify_section(self, section: ContractSection) -> list[str]:
        combined_text = f"{section.article_title}\n{section.text}".lower()
        labels = []

        for label, keywords in self.RELEVANCE_KEYWORDS.items():
            if any(keyword in combined_text for keyword in keywords):
                labels.append(label)

        return labels