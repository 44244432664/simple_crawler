"""Interactive terminal dashboard for Simple Crawler.

The crawler deliberately stays in charge of all download work.  This module
only gathers validated options and passes them to the existing public APIs,
so the terminal interface and JSON batch jobs behave the same way.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from utils.worker_config import DEFAULT_MAX_WORKERS, MAX_WORKERS, validate_max_workers

from api import (
    AI_update,
    ContentType,
    CrawlRequest,
    PackagingMode,
    SelectionMode,
    run_batch,
    run_crawl,
)
from api.contracts import default_output_root


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "outputs"
console = Console()


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    description: str
    action: str


MENU = (
    MenuItem("1", "novel", "Guided single novel download and EPUB build", "novel"),
    MenuItem("2", "multi", "Process a to_crawl.json job list", "multi"),
    MenuItem("3", "epub", "Create an EPUB from an existing novel_info.json", "epub"),
    MenuItem("4", "comic", "Download QQ comic chapters as CBZ or PDF", "comic"),
    MenuItem("5", "gallery", "Download an image gallery as pictures or CBZ", "gallery"),
    MenuItem("6", "ai", "Analyze and register a site format with AI", "ai"),
    MenuItem("7", "db", "Show sites configured in data/aliases.csv", "db"),
    MenuItem("0", "exit", "Close Simple Crawler", "exit"),
)

# These post-UI aliases are additive; the original commands above remain the
# documented interface and will not be renamed again.
COMMAND_ALIASES = {"batch": "multi", "sources": "db"}


def normalize_url(url: str) -> str:
    """Accept URLs with or without a scheme, matching ``crawler.Novel.run``."""
    value = url.strip()
    if value and not value.startswith(("http://", "https://")):
        return f"https://{value}"
    return value


def parse_volume_urls(value: str) -> list[str] | None:
    """Parse the friendly comma-separated form without evaluating user input."""
    urls = [item.strip() for item in value.split(",") if item.strip()]
    return urls or None


def source_for_url(url: str, sources: list[dict[str, str]]) -> dict[str, str] | None:
    """Find a configured source row for a URL, ignoring a leading ``www``."""
    host = urlsplit(normalize_url(url)).netloc.lower().removeprefix("www.")
    for source in sources:
        site = source.get("site", "").strip().lower().removeprefix("www.").rstrip("/")
        if host == site:
            return source
    return None


def build_novel_job(
    *,
    url: str,
    output_dir: str | None,
    sleep_time: int,
    crawl_type: str,
    book_type: str,
    custom_volumes: list[str] | None = None,
    info_url: str | None = None,
    start_chapter: int | None = None,
    end_chapter: int | None = None,
    chapter_url: str | None = None,
    keep_logged_in: bool = False,
    fetch_mode: str | None = None,
    headless: bool = True,
    max_workers: int | None = DEFAULT_MAX_WORKERS,
) -> dict:
    """Return the exact job dictionary accepted by ``crawler.Novel.run``."""
    if not normalize_url(url):
        raise ValueError("A novel URL is required.")
    if crawl_type not in {"full", "range", "single"}:
        raise ValueError("Crawl type must be full, range, or single.")
    if book_type not in {"all", "volume"}:
        raise ValueError("Book type must be all or volume.")
    if sleep_time < 0:
        raise ValueError("Request delay cannot be negative.")
    if crawl_type == "range" and (start_chapter is None or end_chapter is None or start_chapter < 1 or end_chapter < start_chapter):
        raise ValueError("A range needs valid start and end chapter numbers.")
    if crawl_type == "single" and not chapter_url:
        raise ValueError("A single-chapter crawl needs a chapter URL.")
    max_workers = (
        DEFAULT_MAX_WORKERS
        if max_workers is None
        else validate_max_workers(max_workers)
    )

    args: dict[str, object] = {}
    if custom_volumes:
        args["custom_volume_list"] = custom_volumes
    if info_url:
        args["info_url"] = normalize_url(info_url)
    if crawl_type == "range":
        args["start_chapter"] = start_chapter
        args["end_chapter"] = end_chapter
    elif crawl_type == "single":
        args["chapter_url"] = normalize_url(chapter_url or "")

    return {
        "novel_url": normalize_url(url),
        "output_dir": output_dir or None,
        "sleep_time": sleep_time,
        "crawl_type": crawl_type,
        "crawl_type_args": args,
        "book_type": book_type,
        "keep_logged_in": keep_logged_in,
        "fetch_mode": fetch_mode,
        "headless": headless,
        "max_workers": max_workers,
    }


class SimpleCrawlerTUI:
    """A keyboard-first, command-palette style interface for the project."""

    def __init__(self, output: Console = console, input_fn: Callable[[str], str] = input):
        self.console = output
        self.input = input_fn

    def run(self) -> None:
        while True:
            self.render_home()
            try:
                choice = self.input("\n  command › ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[dim]Goodbye.[/]")
                return

            choice = COMMAND_ALIASES.get(choice, choice)
            item = next((entry for entry in MENU if choice in {entry.key, entry.action}), None)
            if item is None:
                self.console.print("[yellow]Unknown command. Use comic, gallery, novel, ai, epub, multi, db, or exit.[/]")
                self.pause()
                continue
            if item.action == "exit":
                self.console.print("[dim]Goodbye.[/]")
                return
            try:
                getattr(self, f"show_{item.action}")()
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Cancelled. Nothing was started.[/]")
                self.pause()
            except Exception as exc:
                self._render_exception("command", exc)
                self.pause()

    def render_home(self) -> None:
        self.console.clear()
        source_count = len(self.read_sources())
        output_count = len(list(OUTPUT_ROOT.iterdir())) if OUTPUT_ROOT.exists() else 0
        title = Text("simple crawler", style="bold bright_cyan")
        subtitle = Text("  local library command center", style="dim")
        self.console.print(Panel(Text.assemble(title, subtitle), border_style="bright_cyan", box=box.ROUNDED))
        self.console.print(f"  [dim]workspace[/] {PROJECT_ROOT}   [dim]sources[/] {source_count}   [dim]output folders[/] {output_count}\n")
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=False)
        table.add_column(style="bold bright_cyan", width=4)
        table.add_column(style="bold green")
        table.add_column(style="dim")
        for item in MENU:
            table.add_row(item.key, item.label, item.description)
        self.console.print(table)
        self.console.print("\n  [dim]Commands: comic, gallery, novel, ai, epub, multi, db, exit. Numbers and aliases also work. Ctrl-C returns here.[/]")

    def ask(self, label: str, default: str | None = None, required: bool = False) -> str:
        # ``input`` writes directly to stdout, so its prompt must remain plain
        # text rather than Rich markup.
        suffix = f" (default: {default})" if default is not None else ""
        while True:
            value = self.input(f"  {label}{suffix}: ").strip()
            if value:
                return value
            if default is not None:
                return default
            if not required:
                return ""
            self.console.print("  [yellow]This value is required.[/]")

    def ask_choice(self, label: str, choices: tuple[str, ...], default: str) -> str:
        allowed = "/".join(choices)
        while True:
            value = self.ask(f"{label} ({allowed})", default).lower()
            if value in choices:
                return value
            self.console.print(f"  [yellow]Choose one of: {allowed}.[/]")

    def ask_yes_no(self, label: str, default: bool = False) -> bool:
        choice = self.ask_choice(label, ("y", "n"), "y" if default else "n")
        return choice == "y"

    def ask_int(
        self,
        label: str,
        default: int | None = None,
        minimum: int = 0,
        maximum: int | None = None,
    ) -> int:
        while True:
            value = self.ask(label, str(default) if default is not None else None, required=default is None)
            try:
                number = int(value)
            except ValueError:
                self.console.print("  [yellow]Enter a whole number.[/]")
                continue
            if number < minimum:
                self.console.print(f"  [yellow]Enter a number of at least {minimum}.[/]")
                continue
            if maximum is not None and number > maximum:
                self.console.print(f"  [yellow]Enter a number of at most {maximum}.[/]")
                continue
            return number

    def confirm(self, message: str = "Start this command?") -> bool:
        return self.ask_yes_no(message, default=True)

    def _collect_crawl_request(self, content_type: ContentType) -> CrawlRequest:
        labels = {
            ContentType.NOVEL: "Novel",
            ContentType.COMIC: "Comic",
            ContentType.GALLERY: "Gallery",
        }
        label = labels[content_type]
        url = self.ask(f"{label} URL", required=True)

        if content_type is ContentType.NOVEL:
            output_format = self.ask_choice("Output format", ("epub", "pdf"), "epub")
            packaging = self.ask_choice("Packaging", ("combined", "per_volume"), "combined")
            selection_choices = ("full", "range", "single")
        else:
            output_format = self.ask_choice("Output format", ("cbz", "pdf", "folder"), "cbz")
            packaging = (
                PackagingMode.PER_CHAPTER.value
                if content_type is ContentType.COMIC
                else PackagingMode.PER_GALLERY.value
            )
            selection_choices = ("full", "range", "single") if content_type is ContentType.COMIC else ("full", "single")

        selection = self.ask_choice("Selection", selection_choices, "full")
        start_index = end_index = None
        chapter_url = None
        if selection == SelectionMode.RANGE.value:
            start_index = self.ask_int("Start chapter", minimum=1)
            end_index = self.ask_int("End chapter", minimum=start_index)
        elif selection == SelectionMode.SINGLE.value:
            chapter_url = self.ask("Chapter URL", required=True)

        sleep_ms = self.ask_int("Delay between requests (milliseconds)", default=1000)
        max_retries = self.ask_int("Retries after a failed request", default=3, minimum=0)
        keep_logged_in = self.ask_yes_no("Keep browser login session?", default=False)
        fetch_mode = self.ask_choice("Fetch mode", ("auto", "requests", "browser"), "auto")
        headless = not self.ask_yes_no("Show browser window?", default=False)
        max_workers = self.ask_int(
            "Chapter workers",
            default=DEFAULT_MAX_WORKERS,
            minimum=1,
            maximum=MAX_WORKERS,
        )
        output_dir = self.ask(
            f"Custom work directory (blank: {default_output_root(content_type)}/<title>)"
        )
        return CrawlRequest(
            url=url,
            content_type=content_type,
            output_format=output_format,
            selection=selection,
            packaging=packaging,
            start_index=start_index,
            end_index=end_index,
            chapter_url=chapter_url,
            output_dir=output_dir or None,
            fetch_mode=fetch_mode,
            headless=headless,
            sleep_ms=sleep_ms,
            max_retries=max_retries,
            max_workers=max_workers,
            keep_logged_in=keep_logged_in,
        )

    def _show_crawl(self, content_type: ContentType, label: str) -> None:
        self.console.print(f"\n[bold bright_cyan]{label} crawl[/]  [dim]URLs may omit https://[/]")
        request = self._collect_crawl_request(content_type)
        self.console.print(
            Panel(
                f"[bold]{request.url}[/]\n"
                 f"{request.selection.value} selection • {request.output_format.value} / "
                 f"{request.packaging.value} • delay: {request.sleep_ms}ms • "
                f"retries: {request.max_retries} • "
                 f"workers: {request.max_workers}",
                title="Ready",
                border_style="green",
            )
        )
        if self.confirm():
            self.execute(label, lambda: self._run_crawl_with_ai_fallback(request))

    def show_novel(self) -> None:
        self._show_crawl(ContentType.NOVEL, "Crawling novel")

    def show_multi(self) -> None:
        self.console.print("\n[bold bright_cyan]Batch crawl[/]")
        self.console.print(
            "  [dim]Use batch v2 JSON: {\"version\": 2, \"jobs\": [...]}.[/]"
        )
        path = self.ask("Batch v2 JSON path", required=True)
        candidate = Path(path)
        if not candidate.exists():
            raise FileNotFoundError(f"No file found at {candidate}")
        with candidate.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if self.confirm(f"Run every job in {candidate}?"):
            self.execute("Running batch", lambda: self._run_batch(payload))

    def show_epub(self) -> None:
        self.console.print("\n[bold bright_cyan]Build EPUB[/]")
        json_path = self.ask("Path to novel_info.json", required=True)
        if not Path(json_path).is_file():
            raise FileNotFoundError(f"No file found at {json_path}")
        output_dir = self.ask("EPUB output directory (blank uses JSON folder)")
        if self.confirm("Build this EPUB?"):
            from ebook.epub import create_epub
            self.execute("Building EPUB", lambda: create_epub(json_path, output_dir or None))

    def show_comic(self) -> None:
        self._show_crawl(ContentType.COMIC, "Crawling comic")

    def show_gallery(self) -> None:
        self._show_crawl(ContentType.GALLERY, "Crawling gallery")

    def _run_crawl_with_ai_fallback(self, request: CrawlRequest) -> None:
        result = run_crawl(request, interactive=True)
        if not result.success and any(
            failure.code == "UnknownSiteError" for failure in result.failures
        ):
            if self.ask_yes_no("Host is not registered. Analyze with AI?", default=False):
                ai_result = self._run_ai_update(request.url, request.content_type)
                if ai_result.success and ai_result.registered:
                    # Reuse the validated request; registration must not restart prompting.
                    result = run_crawl(request, interactive=True)
                else:
                    self._render_ai_result(ai_result)
                    return
        self._render_crawl_result(result)

    def _run_batch(self, payload: object) -> None:
        """Run a batch-v2 payload without adding interactive flow steps."""
        self._render_batch_results(run_batch(payload))

    def _run_ai_update(self, url: str, content_type: ContentType):
        def confirm_repair(errors: tuple[str, ...], attempt: int) -> bool:
            self.console.print(
                Panel(
                    "\n".join(errors),
                    title=f"AI validation needs repair (attempt {attempt})",
                    border_style="yellow",
                )
            )
            return self.ask_yes_no("Ask AI to repair this definition?", default=False)

        def confirm_registration(summary: dict[str, object]) -> bool:
            table = Table(title="AI site definition", box=box.ROUNDED)
            table.add_column("Field", style="bold")
            table.add_column("Value")
            for key in ("content_type", "title_selector", "first_chapter_url", "flow_modules"):
                if key in summary:
                    table.add_row(key, str(summary[key]))
            self.console.print(table)
            return self.confirm("Register this validated site definition?")

        def confirm_draft_overwrite(host: str) -> bool:
            return self.ask_yes_no(
                f"Replace the existing AI draft for {host}?", default=False
            )

        return AI_update(
            url,
            content_type,
            confirm_repair=confirm_repair,
            confirm_registration=confirm_registration,
            confirm_draft_overwrite=confirm_draft_overwrite,
        )

    def show_ai(self) -> None:
        self.console.print("\n[bold bright_cyan]AI site analysis[/]")
        url = self.ask("Site URL", required=True)
        content_type = self.ask_choice("Content type", ("novel", "comic", "gallery"), "novel")
        if self.confirm("Analyze and register this site?"):
            self.execute(
                "Analyzing site with AI",
                lambda: self._render_ai_result(
                    self._run_ai_update(url, ContentType(content_type))
                ),
            )

    def _render_ai_result(self, result: object) -> None:
        if getattr(result, "success", False):
            self.console.print(
                Panel(
                    f"Registered {getattr(result, 'summary', {}).get('content_type', 'site')} "
                    f"format at {getattr(result, 'format_path', None)}.",
                    title="AI registration complete",
                    border_style="green",
                )
            )
            return
        errors = getattr(result, "errors", ()) or ("AI site analysis failed.",)
        details = "\n".join(str(error) for error in errors)
        draft_path = getattr(result, "draft_path", None)
        report_path = getattr(result, "report_path", None)
        if draft_path:
            details += f"\nDraft: {draft_path}"
        if report_path:
            details += f"\nReport: {report_path}"
        self.console.print(Panel(details, title="AI site analysis failed", border_style="red"))

    def _render_crawl_result(self, result: object) -> None:
        if getattr(result, "success", False):
            artifacts = getattr(result, "artifacts", ())
            lines = [
                f"Chapters: {getattr(result, 'chapter_count', 0)}",
                f"Images: {getattr(result, 'image_count', 0)}",
            ]
            lines.extend(f"Artifact: {artifact.path}" for artifact in artifacts)
            self.console.print(Panel("\n".join(lines), title="Crawl complete", border_style="green"))
            return

        failures = getattr(result, "failures", ())
        table = Table(title="Crawl failed", box=box.ROUNDED)
        table.add_column("Stage", style="bold")
        table.add_column("Code")
        table.add_column("Message")
        table.add_column("Chapter")
        table.add_column("URL")
        for failure in failures:
            table.add_row(
                str(failure.stage),
                str(failure.code),
                str(failure.message),
                "" if failure.chapter_ordinal is None else str(failure.chapter_ordinal),
                failure.url or "",
            )
        if failures:
            self.console.print(table)
        else:
            self.console.print(Panel("The crawl did not produce a result.", title="Crawl failed", border_style="red"))

    def _render_batch_results(self, results: list[object]) -> None:
        """Show one concise, structured outcome per batch job."""
        successful = sum(1 for result in results if getattr(result, "success", False))
        table = Table(
            title=f"Batch results ({successful}/{len(results)} successful)",
            box=box.ROUNDED,
        )
        table.add_column("Job", style="bold")
        table.add_column("Result")
        table.add_column("Artifacts")
        table.add_column("Failures")
        for index, result in enumerate(results, start=1):
            artifacts = ", ".join(
                str(getattr(artifact, "path", artifact))
                for artifact in getattr(result, "artifacts", ())
            )
            failures = " | ".join(
                f"{getattr(failure, 'stage', 'batch')}/{getattr(failure, 'code', 'Error')}: "
                f"{getattr(failure, 'message', '')}"
                for failure in getattr(result, "failures", ())
            )
            table.add_row(
                str(index),
                "success" if getattr(result, "success", False) else "failed",
                artifacts or "-",
                failures or "-",
            )
        self.console.print(table)

    def _render_exception(self, stage: str, error: BaseException) -> None:
        table = Table(title="Command failed", box=box.ROUNDED)
        table.add_column("Stage", style="bold")
        table.add_column("Code")
        table.add_column("Message")
        table.add_row(stage, type(error).__name__, str(error) or type(error).__name__)
        self.console.print(table)

    def read_sources(self) -> list[dict[str, str]]:
        aliases = PROJECT_ROOT / "data" / "aliases.csv"
        if not aliases.is_file():
            return []
        with aliases.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def show_db(self) -> None:
        rows = self.read_sources()
        table = Table(title="Configured sources", box=box.ROUNDED, header_style="bold bright_cyan")
        table.add_column("Site")
        table.add_column("Format")
        table.add_column("Crawler")
        for row in rows:
            table.add_row(row.get("site", ""), row.get("name", ""), row.get("crawler_class", ""))
        self.console.print(table)
        self.pause()

    def execute(self, label: str, command: Callable[[], object]) -> None:
        self.console.print(Panel(f"[bold]{label}[/]\n[dim]Crawler output appears below. Ctrl-C stops the current operation.[/]", border_style="bright_cyan"))
        command()
        self.console.print("[bold green]Command finished.[/]")
        self.pause()

    def pause(self) -> None:
        try:
            self.input("\n  Press Enter to return to the command palette…")
        except (EOFError, KeyboardInterrupt):
            pass


def main() -> None:
    SimpleCrawlerTUI().run()


if __name__ == "__main__":
    main()
