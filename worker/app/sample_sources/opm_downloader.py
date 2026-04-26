from pathlib import Path
import hashlib
import re
import time

import pandas as pd
import requests

from worker.app.sample_sources.base import SampleSourceDownloader


class OPMDownloader(SampleSourceDownloader):
    source_name = "opm"

    headers = {"User-Agent": "cba-clock-research-downloader/0.1"}

    @property
    def source_csv(self) -> Path:
        return self.project_root / "data" / "sample_sources" / "opm_cba_sources.csv"

    @property
    def output_dir(self) -> Path:
        return self.project_root / "data" / "samples" / "raw_pdfs"

    @property
    def manifest_csv(self) -> Path:
        return self.project_root / "data" / "sample_sources" / "opm_download_manifest.csv"

    def download(self, limit: int | None = None, overwrite: bool = False) -> pd.DataFrame:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        source_df = pd.read_csv(self.source_csv)
        if limit is not None:
            source_df = source_df.head(limit)

        results = []

        for idx, row in enumerate(source_df.to_dict("records"), start=1):
            print(f"[{idx}/{len(source_df)}] {row['filename']}")
            result = self.download_pdf(row=row, overwrite=overwrite)
            print(result["status"], result.get("error") or "")
            results.append(result)
            time.sleep(0.5)

        manifest_df = pd.DataFrame(results)
        self.manifest_csv.parent.mkdir(parents=True, exist_ok=True)
        manifest_df.to_csv(self.manifest_csv, index=False)

        return manifest_df

    def download_pdf(self, row: dict, overwrite: bool = False) -> dict:
        filename = self.safe_filename(row["filename"])
        output_file = self.output_dir / filename

        result = {
            "source": self.source_name,
            "pdf_url": row["pdf_url"],
            "filename": filename,
            "output_path": str(output_file.relative_to(self.project_root)),
            "status": None,
            "bytes": None,
            "sha256": None,
            "content_type": None,
            "error": None,
        }

        if output_file.exists() and not overwrite:
            content = output_file.read_bytes()
            result["status"] = "skipped_existing"
            result["bytes"] = len(content)
            result["sha256"] = self.sha256_bytes(content)
            return result

        try:
            response = requests.get(row["pdf_url"], headers=self.headers, timeout=60)
            response.raise_for_status()

            content = response.content
            result["content_type"] = response.headers.get("content-type")
            result["bytes"] = len(content)

            if not content.startswith(b"%PDF"):
                result["status"] = "failed_not_pdf"
                result["error"] = "Response did not start with %PDF"
                return result

            output_file.write_bytes(content)
            result["status"] = "downloaded"
            result["sha256"] = self.sha256_bytes(content)
            return result

        except Exception as e:
            result["status"] = "failed"
            result["error"] = repr(e)
            return result

    @staticmethod
    def sha256_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def safe_filename(value: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9._ -]", "", value)
        value = re.sub(r"\s+", "_", value)
        return value[:180]