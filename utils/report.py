import argparse
import csv
import json
import re
from pathlib import Path

STATUS_403_PATTERN = re.compile(r"status code:\s*403\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://\S+")


def find_novel_root(explicit_root: str | None) -> Path:
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Novel root does not exist or is not a directory: {root}")
        return root

    candidates = [
        Path("outputs/Novel"),
        Path("output/Novel"),
        Path("outpus/Novel"),
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()

    raise FileNotFoundError(
        "Could not find a novel root directory. Tried: outputs/Novel, output/Novel, outpus/Novel"
    )


def pick_template_novel_dir(novel_root: Path) -> Path:
    # Use one subfolder as template to infer expected file structure.
    for novel_dir in sorted(novel_root.iterdir(), key=lambda p: p.name.lower()):
        if not novel_dir.is_dir():
            continue
        if (novel_dir / "novel_info.json").is_file() and (novel_dir / "logs" / "crawl_log.txt").is_file():
            return novel_dir

    raise FileNotFoundError(
        f"No template novel folder found under {novel_root} that has novel_info.json and logs/crawl_log.txt"
    )


def load_novel_url(novel_info_path: Path) -> str:
    if not novel_info_path.is_file():
        return ""

    try:
        with novel_info_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""

    if isinstance(data, dict):
        info = data.get("info")
        if isinstance(info, dict):
            value = info.get("novel_url")
            if isinstance(value, str):
                return value

        value = data.get("novel_url")
        if isinstance(value, str):
            return value

    return ""


def count_403_from_log(log_path: Path) -> tuple[int, int]:
    """
    Returns:
        (unique_403_files, total_403_log_hits)
    """
    if not log_path.is_file():
        return 0, 0

    total_hits = 0
    unique_files: set[str] = set()

    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not STATUS_403_PATTERN.search(line):
                    continue

                total_hits += 1

                match = URL_PATTERN.search(line)
                if match:
                    url = match.group(0).rstrip(",.;)]}\"")
                    unique_files.add(url)
    except OSError:
        return 0, 0

    return len(unique_files), total_hits


def build_report(novel_root: Path) -> dict:
    template_dir = pick_template_novel_dir(novel_root)
    novel_info_rel = Path("novel_info.json")
    crawl_log_rel = Path("logs") / "crawl_log.txt"

    rows = []
    for novel_dir in sorted(novel_root.iterdir(), key=lambda p: p.name.lower()):
        if not novel_dir.is_dir():
            continue

        novel_info_path = novel_dir / novel_info_rel
        crawl_log_path = novel_dir / crawl_log_rel

        novel_url = load_novel_url(novel_info_path)
        unique_403_files, total_403_hits = count_403_from_log(crawl_log_path)

        rows.append(
            {
                "novel_name": novel_dir.name,
                "novel_url": novel_url,
                "unique_403_files": unique_403_files,
                "total_403_log_hits": total_403_hits,
                "has_novel_info": novel_info_path.is_file(),
                "has_crawl_log": crawl_log_path.is_file(),
            }
        )

    rows.sort(key=lambda x: (-x["unique_403_files"], x["novel_name"].lower()))

    return {
        "novel_root": str(novel_root),
        "template_novel_dir": str(template_dir),
        "template_paths": {
            "novel_info": str(novel_info_rel),
            "crawl_log": str(crawl_log_rel),
        },
        "summary": {
            "total_novels_scanned": len(rows),
            "novels_with_403": sum(1 for row in rows if row["unique_403_files"] > 0),
            "total_unique_403_files": sum(row["unique_403_files"] for row in rows),
            "total_403_log_hits": sum(row["total_403_log_hits"] for row in rows),
        },
        "novels": rows,
    }


def write_json_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def write_csv_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "novel_name",
                "novel_url",
                "unique_403_files",
                "total_403_log_hits",
                "has_novel_info",
                "has_crawl_log",
            ],
        )
        writer.writeheader()
        writer.writerows(report["novels"])


def print_console_summary(report: dict) -> None:
    summary = report["summary"]
    print(f"Novel root: {report['novel_root']}")
    print(f"Template dir used: {report['template_novel_dir']}")
    print(f"Scanned novels: {summary['total_novels_scanned']}")
    print(f"Novels with 403: {summary['novels_with_403']}")
    print(f"Total unique 403 files: {summary['total_unique_403_files']}")
    print(f"Total 403 log hits: {summary['total_403_log_hits']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a 403 report by scanning each first-level novel folder in outputs/Novel and "
            "reading only novel_info.json + logs/crawl_log.txt."
        )
    )
    parser.add_argument(
        "--novel-root",
        default=None,
        help="Optional path to the novel root folder (default: auto-detect outputs/Novel)",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Output JSON file path (default: <novel_root>/403_report.json)",
    )
    parser.add_argument(
        "--csv-out",
        default=None,
        help="Output CSV file path (default: <novel_root>/403_report.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    novel_root = find_novel_root(args.novel_root)
    report = build_report(novel_root)

    json_out = Path(args.json_out).expanduser().resolve() if args.json_out else novel_root / "403_report.json"
    csv_out = Path(args.csv_out).expanduser().resolve() if args.csv_out else novel_root / "403_report.csv"

    write_json_report(report, json_out)
    write_csv_report(report, csv_out)
    print_console_summary(report)
    print(f"JSON report written to: {json_out}")
    print(f"CSV report written to: {csv_out}")


if __name__ == "__main__":
    main()
