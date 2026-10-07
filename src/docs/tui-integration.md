# TUI Integration

Task 9 routes interactive novel, comic, and gallery commands through the same
`CrawlRequest` and `run_crawl` pipeline used by direct callers.

## Commands

- `novel` asks for EPUB or PDF output and combined or per-volume packaging.
- `comic` asks for CBZ, PDF, or folder output and always uses per-chapter packaging.
- `gallery` asks for CBZ, PDF, or folder output and always uses per-gallery packaging.
- `ai` analyzes and optionally registers a flow-enabled site definition.
- `multi` runs a batch-v2 payload through `run_batch` and renders one structured
  result per job.
- `epub` and `db` retain their existing utility behavior.

All crawl commands ask for selection, delay, login persistence, fetch mode,
headless mode, worker count, and output directory. A gallery does not ask for a
chapter range, and comic/gallery commands do not ask for novel packaging.

## Runtime Behavior

The TUI constructs a validated `CrawlRequest` before confirmation and invokes
`run_crawl(request, interactive=True)`. The runner adds the runtime-only
`get_user_input` step for this path. Direct API and batch calls remain
non-interactive and do not prompt.

The `multi` command requires a JSON file with the batch-v2 envelope:

```json
{
  "version": 2,
  "jobs": [
    {"url": "https://example.com/work", "content_type": "novel", "output_format": "epub"}
  ]
}
```

Legacy list-shaped batch files are intentionally rejected with migration
guidance; the TUI does not route them to a legacy crawler.

If resolution reports `UnknownSiteError`, the TUI offers the AI workflow. A
successful, confirmed registration retries the original request object without
asking for its values again. Declining or cancelling the workflow leaves the
crawl unstarted.

When a failed AI retry would replace an existing draft, the TUI asks for an
explicit overwrite confirmation; otherwise the workflow saves a revision.

Failures are rendered as structured panels/tables containing stage, error code,
message, chapter, and URL. Tracebacks are not shown by default.

## Verification

Run the focused TUI tests with:

```text
python -m pytest TEST/test_tui.py -q
```

Run the complete test directory with:

```text
.Luma/bin/python -m pytest TEST -p no:rerunfailures
```
