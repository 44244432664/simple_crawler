# test site
- https://docln.sbs/truyen/23197-mua-dong-bat-tan-xu-so-nhung-giac-mo-tan-vo

# modify
- plan (gallery feature update)
- Redact tokens, cookies, passwords, and authorization headers from logs at "Remote-browser security" section -> no specific logs, states/issues-only
- usage token -> use api token + TOTP
- `Filesystem and Output Requirements` change:
    - `/data/` folder to
    ```
    api/online-data/
        api.sqlite3
        jobs/{job-id}/
            output-path.txt
        uploads/{upload-id}
        browser-profiles/{source-key}/
        runtime/
    ```
    - where `output-path.txt` contains the path to file crawled (e.g `ouput` foler) on the computer (server)
- change the comic/image type file organization into similar to `output/Novel/` folder
- add api `GET /api/v1/books` for viewing book titles available on output folder for re-downloads (book folder that does not have epub/cbz/pdf file will raise error in book download later)

# current project's status
## status
- api/tui import successfully
- `data` folder incompatible (intentionally)
- `data/secrets.json` has no api in ai integration (your fault)
- PDF depencies exist locally, but dependencies manifest incomplete
- ONLY python api, no Fast/REST API or web based service to run (it is only in your head LOL)
## BUG BUG BUG (from both AI and U)
- basic implementation completed, has A LOT to fix
- delay in Request path is ignored (this may lead to site block
- retry config (max_retries) has no use in real run
- pdf installation, exporter, etc. not reproducible (not exportable)
- `novel_info.json` failed (not write)
- image handling/downloading need review (theres A LOT of problems that you need a full report for it)
- output layout (output path) is confusing YOU NEED TO FIX THIS. MAKE THEM GO UNDER ONE DIR
- fall back cover enforcement problems (eh, it can wait)
- browser/cloudflare behavior is not regularized
- page discovery can return less than needed (yes, test this shit)


# future note
- run chrome profile in (Docker) container???
