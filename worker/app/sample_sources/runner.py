from pathlib import Path
import argparse

from worker.app.sample_sources.opm_downloader import OPMDownloader
from worker.app.sample_sources.dol_downloader import DOLDownloader

DOWNLOADER_REGISTRY = {
    OPMDownloader.source_name: OPMDownloader,
    DOLDownloader.source_name: DOLDownloader,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download sample CBA source documents."
    )
    parser.add_argument("source", choices=sorted(DOWNLOADER_REGISTRY))
    parser.add_argument(
        "--limit", type=int, default=None, help="Max number of CBAs to download"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Re-download files that already exist"
    )
    parser.add_argument(
        "--refresh-source",
        action="store_true",
        help="Re-fetch the source list from the API (DOL only)",
    )

    # DOL-specific filters
    dol_filters = parser.add_argument_group("DOL filters")
    dol_filters.add_argument(
        "--union",
        type=str,
        default=None,
        help="Filter by union name substring (e.g. 'AFGE', 'SEIU')",
    )
    dol_filters.add_argument(
        "--employer", type=str, default=None, help="Filter by employer name substring"
    )
    dol_filters.add_argument(
        "--state",
        type=str,
        default=None,
        help="Filter by state/location (e.g. 'NY', 'CA')",
    )
    dol_filters.add_argument(
        "--sector",
        type=str,
        default=None,
        choices=["PRIVATE", "PUBLIC"],
        help="Filter by sector type",
    )
    dol_filters.add_argument(
        "--naics",
        type=str,
        default=None,
        help="Filter by NAICS code prefix (e.g. '517' for telecom)",
    )
    dol_filters.add_argument(
        "--exp-year-min",
        type=int,
        default=None,
        help="Only include CBAs expiring on or after this year",
    )

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    downloader = DOWNLOADER_REGISTRY[args.source](project_root=project_root)

    if args.refresh_source and hasattr(downloader, "fetch_source_list"):
        downloader.fetch_source_list()
        return

    if args.source == "dol":
        downloader.download(
            limit=args.limit,
            overwrite=args.overwrite,
            union=args.union,
            employer=args.employer,
            state=args.state,
            sector=args.sector,
            naics=args.naics,
            exp_year_min=args.exp_year_min,
        )
    else:
        downloader.download(limit=args.limit, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
