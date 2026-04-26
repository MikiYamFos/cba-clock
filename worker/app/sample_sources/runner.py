from pathlib import Path
import argparse

from worker.app.sample_sources.opm_downloader import OPMDownloader


DOWNLOADER_REGISTRY = {
    OPMDownloader.source_name: OPMDownloader,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Download sample CBA source documents.")
    parser.add_argument("source", choices=sorted(DOWNLOADER_REGISTRY))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    downloader = DOWNLOADER_REGISTRY[args.source](project_root=project_root)
    downloader.download(limit=args.limit, overwrite=args.overwrite)


if __name__ == "__main__":
    main()