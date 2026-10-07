# Integration And Acceptance Guide

Task 10 is the release gate for the flow-driven crawler. The supported runtime
path is the unified API; the TUI and batch runner are adapters over it.

## CrawlRequest

`CrawlRequest` is the normalized request shared by TUI, API, and batch callers.
The required fields are `url`, `content_type`, and `output_format`.

Optional fields include `selection`, `start_index`, `end_index`, `chapter_url`,
`packaging`, `output_dir`, `fetch_mode`, `headless`, `sleep_ms`,
`max_workers`, and `keep_logged_in`. Construction validates the URL, selection,
worker/delay values, and the content/output/packaging combination without
network or filesystem side effects.

The batch-v2 envelope is:

```json
{
  "version": 2,
  "jobs": [
    {
      "url": "https://example.com/work",
      "content_type": "novel",
      "output_format": "epub",
      "selection": "full"
    }
  ]
}
```

Use `api.run_crawl(request)` for one request and `api.run_batch(payload)` for a
batch. Both return structured results; batch results preserve input order.
Legacy list-shaped jobs and crawler-specific entry points are rejected instead
of bypassing flow validation.

## Output Matrix

| Content | Formats | Packaging |
| --- | --- | --- |
| Novel | `epub`, `pdf` | `combined`, `per_volume` |
| Comic | `cbz`, `pdf`, `folder` | `per_chapter` |
| Gallery | `cbz`, `pdf`, `folder` | `per_gallery` |

Successful results contain openable artifacts. Incomplete chapter/image work,
export errors, and invalid combinations produce `success: false`; no final
artifact is reported as successful.

## Flow Schema

Stored formats must contain a matching `content_type` and an ordered `flow`:

```json
{
  "content_type": "novel",
  "flow": [
    {"module": "get_metadata", "params": {}},
    {"module": "volumes_prepare", "params": {}},
    {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
    {"module": "create_ebook", "params": {}}
  ]
}
```

Only allowlisted modules and parameters are accepted. The stored flow always
uses metadata, volume preparation, chapter crawling, and export in that order.
The TUI may prepend the runtime-only `get_user_input` step; API and batch paths
never execute it.

## AI Configuration

The active provider configuration is read from the ignored
`data/secrets.json`. It uses the `AI_API.provider`, `AI_API.api_key`, and
`AI_API.model` fields, with optional `AI_API.base_url` and timeout settings.
Ollama may omit an API key. The tracked system prompt is
`data/system_prompt/ai_site_generation_v1.txt`.

Failed or declined definitions are stored as redacted, bounded drafts under
`data/drafts/<host>/`. Registration requires deterministic validation and an
explicit confirmation callback. Existing formats and aliases are never
overwritten. No `.env` file is read or created.

## Breaking Cutover

Existing formats without a `flow` fail with `MissingFlowError` before fetching
or creating output directories. Legacy crawler wrappers fail with the same
cutover guidance. Legacy batch payloads fail with migration guidance to the
`{"version": 2, "jobs": [...]}` envelope.

Existing files under `data/formats` and `data/template` are protected by SHA-256
checksum tests. New fixture formats belong in temporary test directories and
must not modify those protected files.

## Verification

Run Task 10 acceptance tests:

```bash
.Luma/bin/python -m pytest TEST/test_task10_acceptance.py -v
```

Run the focused pipeline and acceptance tests:

```bash
.Luma/bin/python -m pytest \
  TEST/test_task10_acceptance.py \
  TEST/test_run_crawl.py \
  TEST/test_flow.py \
  TEST/test_export.py \
  -p no:rerunfailures
```

Run the complete suite:

```bash
.Luma/bin/python -m pytest TEST -p no:rerunfailures
```

The Selenium service-resolution check is skipped when a restricted environment
forbids local socket allocation. In a normal acceptance environment it runs as
usual; the Task 10 fixture tests themselves do not require Selenium or live
services.
