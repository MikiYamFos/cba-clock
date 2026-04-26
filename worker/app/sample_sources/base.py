from abc import ABC, abstractmethod
from pathlib import Path
import pandas as pd


class SampleSourceDownloader(ABC):
    source_name: str

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    @abstractmethod
    def download(self, limit: int | None = None, overwrite: bool = False) -> pd.DataFrame:
        pass