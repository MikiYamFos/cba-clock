from pathlib import Path
import hashlib
import json
import re
import time
import urllib3

import pandas as pd
import requests

from worker.app.sample_sources.base import SampleSourceDownloader

# DOL OLMS uses a government certificate chain not included in certifi's bundle.
# SSL verification is disabled for this known government domain.
# See SETUP.md for details.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DOLDownloader(SampleSourceDownloader):
    source_name = "dol"

    FILER_LIST_URL = "https://olmsapps.dol.gov/olpdr/GetCBAFilerListServlet"
    ATTACHMENT_URL = "https://olmsapps.dol.gov/olpdr/GetAttachmentServlet"
    VERIFY_SSL = False  # DOL uses government cert chain not in certifi bundle

    headers = {"User-Agent": "cba-clock-research-downloader/0.1"}

    @property
    def source_csv(self) -> Path:
        return self.project_root / "data" / "sample_sources" / "dol_cba_sources.csv"

    @property
    def output_dir(self) -> Path:
        return self.project_root / "data" / "samples" / "raw_pdfs"

    @property
    def manifest_csv(self) -> Path:
        return (
            self.project_root / "data" / "sample_sources" / "dol_download_manifest.csv"
        )

    def fetch_source_list(self) -> pd.DataFrame:
        """Hit the DOL API once and save the full CBA list as a source CSV."""
        print("Fetching CBA list from DOL OLMS...")
        response = requests.post(
            self.FILER_LIST_URL,
            headers=self.headers,
            timeout=60,
            verify=self.VERIFY_SSL,
        )
        response.raise_for_status()

        data = json.loads(response.content.decode("latin-1"))
        filers = data["filerList"]

        rows = []
        for f in filers:
            rows.append(
                {
                    "doc_id": f["docId"],
                    "cba_id": f["cbaId"],
                    "emp_name": f.get("empName", ""),
                    "union_name": f.get("unionName", ""),
                    "location": f.get("location", ""),
                    "naics": f.get("naics", ""),
                    "no_of_emp": f.get("noOfEmp", ""),
                    "exp_date_ts": f.get("expDate", ""),
                    "type": f.get("type", ""),
                    "agreement_filename": f.get("agreementFileName", ""),
                    "pdf_url": f"{self.ATTACHMENT_URL}?docId={f['docId']}",
                    "filename": self.safe_filename(f),
                }
            )

        df = pd.DataFrame(rows)
        df["exp_year"] = pd.to_datetime(
            df["exp_date_ts"], unit="ms", errors="coerce"
        ).dt.year

        self.source_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.source_csv, index=False)
        print(f"Saved {len(df)} CBA records to {self.source_csv}")
        return df

    def download(
        self,
        limit: int | None = None,
        overwrite: bool = False,
        union: str | None = None,
        employer: str | None = None,
        state: str | None = None,
        sector: str | None = None,
        naics: str | None = None,
        exp_year_min: int | None = None,
    ) -> pd.DataFrame:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not self.source_csv.exists():
            source_df = self.fetch_source_list()
        else:
            source_df = pd.read_csv(self.source_csv)
            print(f"Using existing source list: {len(source_df)} records")

        if union:
            source_df = source_df[
                source_df["union_name"].str.contains(union, case=False, na=False)
            ]
            print(f"  After union filter '{union}': {len(source_df)} records")

        if employer:
            source_df = source_df[
                source_df["emp_name"].str.contains(employer, case=False, na=False)
            ]
            print(f"  After employer filter '{employer}': {len(source_df)} records")

        if state:
            source_df = source_df[
                source_df["location"].str.contains(state, case=False, na=False)
            ]
            print(f"  After state filter '{state}': {len(source_df)} records")

        if sector:
            source_df = source_df[source_df["type"].str.upper() == sector.upper()]
            print(f"  After sector filter '{sector}': {len(source_df)} records")

        if naics:
            source_df = source_df[source_df["naics"].astype(str).str.startswith(naics)]
            print(f"  After NAICS filter '{naics}': {len(source_df)} records")

        if exp_year_min:
            source_df = source_df[source_df["exp_year"] >= exp_year_min]
            print(
                f"  After exp_year_min filter '{exp_year_min}': {len(source_df)} records"
            )

        if limit is not None:
            source_df = source_df.head(limit)

        print(f"Downloading {len(source_df)} CBAs...")
        results = []
        for idx, row in enumerate(source_df.to_dict("records"), start=1):
            print(f"[{idx}/{len(source_df)}] {row['filename']}")
            result = self.download_pdf(row=row, overwrite=overwrite)
            print(f"  {result['status']}", result.get("error") or "")
            results.append(result)
            time.sleep(0.5)

        manifest_df = pd.DataFrame(results)
        self.manifest_csv.parent.mkdir(parents=True, exist_ok=True)
        manifest_df.to_csv(self.manifest_csv, index=False)
        return manifest_df

    def download_pdf(self, row: dict, overwrite: bool = False) -> dict:
        filename = row["filename"]
        output_file = self.output_dir / filename

        result = {
            "source": self.source_name,
            "doc_id": row["doc_id"],
            "pdf_url": row["pdf_url"],
            "emp_name": row["emp_name"],
            "union_name": row["union_name"],
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
            response = requests.get(
                row["pdf_url"],
                headers=self.headers,
                timeout=60,
                verify=self.VERIFY_SSL,
            )
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
    def safe_filename(filer: dict) -> str:
        doc_id = filer["docId"]
        emp = re.sub(r"[^a-zA-Z0-9 ]", "", filer.get("empName", ""))
        emp = re.sub(r"\s+", "_", emp.strip())
        return f"dol_{doc_id}_{emp[:60]}.pdf"
