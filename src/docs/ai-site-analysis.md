# AI Site Analysis

Task 8 is exposed through `api.ai_update` and re-exported from `api`.

## Workflow

Call `AI_update(url, content_type, ...)` with `content_type` set explicitly to
`novel`, `comic`, or `gallery`. The workflow fetches and compacts the main page,
asks the configured provider for list selectors and an actual first chapter
anchor, fetches that chapter, and asks for the completed format and flow.

Generated definitions are validated locally before registration. Validation
checks selector matches, required values, URL safety, decimal chapter naming,
content-specific image/text actions, and the `get_metadata` ->
`volumes_prepare` -> `crawl_chapter` -> `create_ebook` flow.

The first-stage response must already provide a matching title selector, usable
volume/gallery chapter links, ordering, and naming rules before its chapter URL
is fetched. Final definitions reject unknown fields, URL credentials, sensitive
query values, and credential-shaped fields.

`confirm_repair(errors, attempt)` is called before each repair. At most two
repair calls are made. `confirm_registration(summary)` must return true before
anything is written to the format or alias directories. Callbacks may also
accept one argument or no arguments.

When a new failed attempt would replace an existing host draft,
`confirm_draft_overwrite(host)` is called. If it is absent or declines, the
attempt is retained as `candidate-<n>.json` and `report-<n>.json` in that same
host directory instead of replacing the original draft.

## Configuration and drafts

The provider is configured by Task 7 through `data/secrets.json`; the tracked
system prompt is `data/system_prompt/ai_site_generation_v1.txt`. No `.env` file
is read or created.

Failed, declined, or exhausted candidates are atomically written to
`data/drafts/<host>/candidate.json` and `report.json` (or the `drafts_dir`
supplied by the caller). Drafts contain bounded, redacted candidate data and
validation messages, never full HTML, credentials, or authorization headers.

## Registration

`register_site_definition()` accepts only the mapping returned by
`validate_site_definition()` and requires `confirmed=True`; normal callers use
`AI_update()` after its registration confirmation callback. It atomically
creates a new format JSON and appends a `site,name,FlowCrawler` alias row.
Existing format names and hosts are collisions; they are never overwritten. If
alias persistence fails after the format is created, the new format is removed.

## Tests

Run the focused tests with:

```bash
.Luma/bin/python -m pytest TEST/test_ai_update.py -v
```

The tests use mocked pages and AI responses and temporary format, alias, and
draft paths. No live website or AI provider is contacted.
