"""Interactive terminal dashboard for Simple Crawler.

The crawler deliberately stays in charge of all download work.  This module
only gathers validated options and passes them to the existing public APIs,
so the terminal interface and JSON batch jobs behave the same way.
"""

from __future__ import annotations

import csv
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
    MenuItem("5", "db", "Show sites configured in data/aliases.csv", "db"),
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
                self.console.print("[yellow]Unknown command. Use comic, novel, epub, multi, db, or exit.[/]")
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
                self.console.print(f"[bold red]Could not complete that command:[/] {exc}")
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
        self.console.print("\n  [dim]Original commands: comic, novel, epub, multi, db, exit. Numbers and aliases also work. Ctrl-C returns here.[/]")

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

    def show_novel(self) -> None:
        self.console.print("\n[bold bright_cyan]New novel crawl[/]  [dim]URLs may omit https://[/]")
        url = self.ask("Novel URL", required=True)
        source = source_for_url(url, self.read_sources())
        output_dir = self.ask("Output directory (blank uses outputs/Novel)")
        sleep_time = self.ask_int("Delay between requests (milliseconds)", default=1000)
        is_generated_url_source = bool(source and source.get("crawler_class") == "XCrawler")
        if is_generated_url_source:
            crawl_type = "range"
            self.console.print("  [dim]This source generates chapter URLs, so a chapter range is required.[/]")
        else:
            crawl_type = self.ask_choice("Crawl", ("full", "range", "single"), "full")
        custom_volumes = None if is_generated_url_source else parse_volume_urls(self.ask("Custom volume URLs (comma separated, optional)"))
        info_url = self.ask("Novel info URL (optional)") if custom_volumes else ""
        start_chapter = end_chapter = None
        chapter_url = ""
        if crawl_type == "range":
            start_chapter = self.ask_int("Start chapter", minimum=1)
            end_chapter = self.ask_int("End chapter", minimum=start_chapter)
        elif crawl_type == "single":
            chapter_url = self.ask("Chapter URL", required=True)
        book_type = self.ask_choice("Build EPUB", ("all", "volume"), "all")
        keep_logged_in = self.ask_yes_no("Keep browser login session?", default=False)
        fetch_choice = self.ask_choice("Fetch mode", ("auto", "site", "requests", "browser"), "auto")
        headless = not self.ask_yes_no("Show browser window?", default=False)
        max_workers = self.ask_int(
            "Chapter workers",
            default=DEFAULT_MAX_WORKERS,
            minimum=1,
            maximum=MAX_WORKERS,
        )
        job = build_novel_job(
            url=url, output_dir=output_dir or None, sleep_time=sleep_time, crawl_type=crawl_type,
            book_type=book_type, custom_volumes=custom_volumes, info_url=info_url or None,
            start_chapter=start_chapter, end_chapter=end_chapter, chapter_url=chapter_url,
            keep_logged_in=keep_logged_in, fetch_mode=None if fetch_choice == "site" else fetch_choice,
            headless=headless, max_workers=max_workers,
        )
        self.console.print(Panel(f"[bold]{job['novel_url']}[/]\n{crawl_type} crawl • EPUB: {book_type} • delay: {sleep_time}ms • workers: {max_workers}", title="Ready", border_style="green"))
        if self.confirm():
            from crawler.Novel import run
            self.execute("Crawling novel", lambda: run(**job))

    def show_multi(self) -> None:
        self.console.print("\n[bold bright_cyan]Batch crawl[/]")
        path = self.ask("Path to to_crawl.json", "to_crawl.json")
        candidate = Path(path)
        if not candidate.exists():
            raise FileNotFoundError(f"No file found at {candidate}")
        if self.confirm(f"Run every job in {candidate}?"):
            from utils.crawl_multi import crawl_multi
            self.execute("Running batch", lambda: crawl_multi(str(candidate)))

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
        self.console.print("\n[bold bright_cyan]QQ comic crawl[/]")
        url = self.ask("QQ comic URL", required=True)
        action = self.ask_choice("Download", ("all", "range", "single"), "all")
        delete_page_0 = self.ask_yes_no("Remove the first image from each chapter?", default=False)
        make_cbz = self.ask_yes_no("Create CBZ files? (no creates PDFs)", default=True)
        delete_chapter_dir = self.ask_yes_no("Delete images after packaging?", default=True)
        args: list[object] = [delete_page_0, make_cbz, delete_chapter_dir]
        action_name = "get_all"
        if action == "range":
            start = self.ask_int("Start chapter", minimum=1)
            end = self.ask_int("End chapter", minimum=start)
            args = [start, end, *args]
            action_name = "get_chapter_range"
        elif action == "single":
            args = [self.ask_int("Chapter", minimum=1), *args]
            action_name = "get_chapter"
        if self.confirm():
            from crawler.crawl_qq import QQCrawler, control_QQcrawler
            self.execute("Crawling comic", lambda: control_QQcrawler(QQCrawler(normalize_url(url)), action_name, *args))

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
