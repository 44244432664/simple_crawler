import argparse
import json
import os
import sys
# from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
# from typing import Any

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])


# def _load_jobs(_json_path: str) -> list[dict[str, Any]]:
#     json_path = os.path.join(_json_path, "to_crawl.json") if os.path.isdir(_json_path) else _json_path
#     if not os.path.exists(json_path):
#         raise FileNotFoundError(f"JSON file not found: {json_path}")

#     with open(json_path, "r", encoding="utf-8") as f:
#         data = json.load(f)

#     if not isinstance(data, list):
#         raise ValueError("Input JSON must be a list of dictionaries.")

#     jobs: list[dict[str, Any]] = []
#     for i, item in enumerate(data, start=1):
#         if not isinstance(item, dict):
#             raise ValueError(f"Item #{i} is not a dictionary.")

#         if not item.get("novel_url"):
#             raise ValueError(f"Item #{i} is missing required key: 'novel_url'.")

#         jobs.append(item)

#     return jobs


def _get_run_callable():
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    try:
        from crawler.Novel import run as crawler_run
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown"
        if missing == "crawler":
            raise ModuleNotFoundError(
                "Cannot import 'crawler'. Make sure you run this script from the project root, "
                "or keep this script inside the same project tree."
            ) from exc
        raise ModuleNotFoundError(
            f"Missing dependency '{missing}'. Install project dependencies first."
        ) from exc
    return crawler_run


def _resolve_json_path(path: str) -> str:
    return os.path.join(path, "to_crawl.json") if os.path.isdir(path) else path


def _load_jobs(path: str) -> list[dict]:
    json_path = _resolve_json_path(path)
    with open(json_path, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    if not isinstance(jobs, list):
        raise ValueError("Input JSON must be a list of crawl argument objects.")
    return jobs


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip("/")


def _resolve_report_path(path: str | None = None) -> str:
    if path:
        if os.path.isdir(path):
            return os.path.join(path, "403_report.json")
        return path

    candidates = [
        "outputs/Novel/403_report.json",
        "output/Novel/403_report.json",
        "outpus/Novel/403_report.json",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[0]


def _load_403_urls(report_path: str) -> list[str]:
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    novels = report.get("novels") if isinstance(report, dict) else None
    if not isinstance(novels, list):
        raise ValueError("Invalid report format: missing novels list.")

    urls: list[str] = []
    seen: set[str] = set()
    for item in novels:
        if not isinstance(item, dict):
            continue

        url = _normalize_url(item.get("novel_url"))
        if not url:
            continue

        try:
            unique_403_files = int(item.get("unique_403_files", 0) or 0)
        except (TypeError, ValueError):
            unique_403_files = 0
        try:
            total_403_log_hits = int(item.get("total_403_log_hits", 0) or 0)
        except (TypeError, ValueError):
            total_403_log_hits = 0
        if unique_403_files == 0 and total_403_log_hits == 0:
            continue

        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    return urls


# def _run_job(index: int, total: int, job_args: dict[str, Any]) -> dict[str, Any]:
#     novel_url = job_args.get("novel_url", "")
#     print(f"[{index}/{total}] Starting: {novel_url}")

#     try:
#         run = _get_run_callable()
#         run(**job_args)
#         print(f"[{index}/{total}] Completed: {novel_url}")
#         return {
#             "index": index,
#             "novel_url": novel_url,
#             "status": "success",
#             "error": None,
#         }
#     except Exception as exc:
#         print(f"[{index}/{total}] Failed: {novel_url} | Error: {exc}")
#         return {
#             "index": index,
#             "novel_url": novel_url,
#             "status": "failed",
#             "error": str(exc),
#         }


# def crawl_multi(json_path: str, max_workers: int = 4) -> list[dict[str, Any]]:
#     """
#     Crawl multiple novels from a JSON file.

#     Args:
#         json_path: Path to a JSON file containing a list of dictionaries.
#             Each dictionary is passed directly to crawler.Novel.run(**kwargs).
#         max_workers: Number of worker threads.

#     Returns:
#         List of result dictionaries with status for each job.
#     """
#     jobs = _load_jobs(json_path)
#     total = len(jobs)

#     if total == 0:
#         print("No jobs found in JSON file.")
#         return []

#     workers = max(1, min(max_workers, total))
#     print(f"Loaded {total} crawl job(s). Using {workers} thread(s).")

#     results: list[dict[str, Any]] = []

#     if workers == 1:
#         for idx, job in enumerate(jobs, start=1):
#             results.append(_run_job(idx, total, job))
#         return sorted(results, key=lambda x: x["index"])

#     with ThreadPoolExecutor(max_workers=workers) as executor:
#         future_map = {
#             executor.submit(_run_job, idx, total, job): idx
#             for idx, job in enumerate(jobs, start=1)
#         }

#         for future in as_completed(future_map):
#             idx = future_map[future]
#             try:
#                 result = future.result()
#             except Exception as exc:
#                 result = {
#                     "index": idx,
#                     "novel_url": jobs[idx - 1].get("novel_url", ""),
#                     "status": "failed",
#                     "error": str(exc),
#                 }
#             results.append(result)

#     return sorted(results, key=lambda x: x["index"])


# def main() -> None:
#     parser = argparse.ArgumentParser(
#         description=(
#             "Crawl multiple novels from a JSON file. Each item in the JSON list "
#             "is passed to crawler.Novel.run(**kwargs)."
#         )
#     )
#     parser.add_argument("json_path", help="Path to to_crawl.json")
#     parser.add_argument(
#         "--workers",
#         type=int,
#         default=4,
#         help="Number of worker threads (default: 4)",
#     )

#     args = parser.parse_args()
#     results = crawl_multi(args.json_path, max_workers=args.workers)

#     success_count = sum(1 for r in results if r["status"] == "success")
#     failed = [r for r in results if r["status"] == "failed"]

#     print("\nSummary")
#     print(f"Success: {success_count}/{len(results)}")
#     print(f"Failed: {len(failed)}")

#     if failed:
#         print("Failed jobs:")
#         for item in failed:
#             print(f"- #{item['index']} {item['novel_url']} | {item['error']}")


# if __name__ == "__main__":
#     main()


def crawl_multi(path: str, index: list[int] | None = None):
    json_path = _resolve_json_path(path)
    args = _load_jobs(path)
    run = _get_run_callable()

    if os.path.exists("crawl_multi.log"):
        # delete old log file
        os.remove("crawl_multi.log")

    log_file = open("crawl_multi.log", "a", encoding="utf-8")
    log_file.write(f"Loaded {len(args)} crawl job(s) from {json_path}\n")
    log_file.flush()

    if index is not None:
        args = [args[i] for i in index if 0 <= i < len(args)]

    for i, arg in enumerate(args):
        print(f"\n\n\nProcessing job {i + 1}/{len(args)}")
        log_file.write(f"Processing job {i + 1}/{len(args)}: {arg.get('novel_url')}\n")
        log_file.flush()
        
        try:
            run(**arg)
        except Exception as exc:
            print(f"Error processing job {i + 1}: {exc}. Check crawl_multi.log for details.")
            log_file.write(f"Error processing job {i + 1}: {exc}\nAt arguments: {arg}\n")
            log_file.flush()

    log_file.close()


def retry(path: str = "to_crawl.json", report_path: str = "outputs/Novel/403_report.json"):
    """
    Retry crawling entries from to_crawl.json using novel URLs from 403_report.json.
    Only URLs with unique_403_files != 0 or total_403_log_hits != 0 are retried.

    Args:
        path: Path to to_crawl.json (or a directory that contains it).
        report_path: Path to 403_report.json.

    Returns:
        dict: Summary with matched/success/failed counts.
    """
    json_path = _resolve_json_path(path)
    report_json_path = _resolve_report_path(report_path)
    urls_to_retry = _load_403_urls(report_json_path)
    jobs = _load_jobs(path)

    result = {
        "json_path": json_path,
        "report_path": report_json_path,
        "urls_from_report": len(urls_to_retry),
        "urls_with_matches": 0,
        "matched_jobs": 0,
        "success": 0,
        "failed": 0,
        "missing_urls": [],
    }

    if not urls_to_retry:
        print(f"No 403 URLs found in report: {report_json_path}")
        return result

    run = _get_run_callable()
    jobs_by_url: dict[str, list[dict]] = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        url = _normalize_url(job.get("novel_url"))
        if not url:
            continue
        jobs_by_url.setdefault(url, []).append(job)

    with open("crawl_multi.log", "a", encoding="utf-8") as log_file:
        log_file.write(
            f"Retry requested from report {report_json_path}. URLs with 403: {len(urls_to_retry)}\n"
        )
        log_file.flush()

        for report_i, url in enumerate(urls_to_retry, start=1):
            matched_jobs = jobs_by_url.get(url, [])
            if not matched_jobs:
                result["missing_urls"].append(url)
                print(f"No crawl arguments found for URL: {url}")
                log_file.write(f"No crawl arguments found for URL: {url}\n")
                log_file.flush()
                continue

            result["urls_with_matches"] += 1
            result["matched_jobs"] += len(matched_jobs)

            for job_i, arg in enumerate(matched_jobs, start=1):
                print(
                    f"Retrying URL {report_i}/{len(urls_to_retry)}, "
                    f"job {job_i}/{len(matched_jobs)}: {arg.get('novel_url')}"
                )
                log_file.write(
                    f"Retrying URL {report_i}/{len(urls_to_retry)}, "
                    f"job {job_i}/{len(matched_jobs)}: {arg.get('novel_url')}\n"
                )
                log_file.flush()

                try:
                    run(**arg)
                    result["success"] += 1
                except Exception as exc:
                    result["failed"] += 1
                    print(f"Retry failed for URL {url}: {exc}")
                    log_file.write(f"Retry failed for URL {url}: {exc}\nAt arguments: {arg}\n")
                    log_file.flush()

    return result


def _parse_cli_args() -> argparse.Namespace:
    # run with: python -m utils.crawl_multi crawl_multi --path path/to/to_crawl.json --index 0 2 5
    # or: python -m utils.crawl_multi retry --path path/to/to_crawl.json --report-path path/to/403_report.json
    parser = argparse.ArgumentParser(
        description="Run crawl_multi or retry from command line."
    )
    parser.add_argument(
        "mode",
        choices=["crawl_multi", "retry"],
        help="Mode to run.",
    )
    parser.add_argument(
        "--path",
        default="to_crawl.json",
        help="Path to to_crawl.json, or a directory containing it (default: to_crawl.json).",
    )
    parser.add_argument(
        "--index",
        nargs="+",
        type=int,
        help="Indices of jobs to run (0-based).",
    )

    parser.add_argument(
        "--report-path",
        default="outputs/Novel/403_report.json",
        help="Path to 403_report.json (used in retry mode).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = _parse_cli_args()

    if cli_args.mode == "crawl_multi":
        crawl_multi(cli_args.path, index=cli_args.index)
    else:
        print(retry(path=cli_args.path, report_path=cli_args.report_path))


