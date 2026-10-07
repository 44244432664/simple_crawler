# Security policy and local handling

## Credentials and browser state

- Store local credentials only in `data/secrets.json` (or an ignored `.env`
  file). These paths must never be added to Git.
- Do not copy API keys into tests, documentation, issue reports, terminal logs,
  or site-format JSON files.
- `.crawler_profiles/` contains Chrome cookies and other browser session data.
  Keep it ignored and owner-readable only; remove it when a browser session is
  no longer needed.
- Run `.Luma/bin/python scripts/check_secrets.py` before committing. It scans
  tracked files and reports only the file and rule name, never the value.

## If a credential was exposed

1. Revoke or rotate it at the issuing provider immediately. Removing a value
   from a current file does not make a previously exposed key safe.
2. Remove the value from every reachable Git revision and force-push the
   rewritten history. Local branches, stashes, and recovery branches count as
   reachable history.
3. Ask collaborators to re-clone or carefully reset after the rewrite. GitHub
   forks, caches, logs, and copied artifacts may still contain the old value.
4. Remove local browser profiles and generated debug pages if they may contain
   authenticated content.

## Current local audit status

The active files do not contain high-confidence Google, GitHub, AWS, or
private-key patterns. A local `data/secrets.json` is ignored with mode `0600`.
However, historic local revisions that previously tracked that path remain a
credential-exposure risk until the history is rewritten and the credential is
rotated.
