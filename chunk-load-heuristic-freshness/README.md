# chunk-load-heuristic-freshness

A Vite app (entry JS plus one chunk loaded when a button is pressed) is served by nginx with no `Cache-Control`. Each user fetches v1 in Chromium, then the v1 container is replaced by a v2 container that no longer has the v1 hashed JS, and the user opens the site again and presses the button. The run records which version boots, whether the chunk loads, Navigation/Resource Timing `transferSize`, console messages, and the nginx access log lines for each user.

## Variables

- A: age of `index.html` when it is fetched (`Date` minus `Last-Modified`). The container sets the mtime of every served file to `start - AGE_SEC` in `server/entrypoint.sh`.
- r: time from fetch to revisit. It is produced in one of two ways:
  - `real-wait`: the runner waits r seconds before revisiting (calibration only)
  - `age-header`: the v1 container serves `index.html` with `Age: <r>` and the user revisits right after the deploy. The recorded r is the `Age` value plus the seconds that actually passed between fetch and revisit.
- The boundary is judged with the measured `r / A` (`measured.r_over_A` in `results.json`), not the target values.

## Layout

- `app/` Vite app. `VITE_APP_VERSION` changes every hashed file name between v1 and v2. `VITE_RELOAD_ON_PRELOAD_ERROR=1` adds a `vite:preloadError` handler that reloads once.
- `server/Dockerfile` builds one site image (`site-repro:v1`, `v2`, `v2-keep`, `v1-reload`, `v2-reload`).
- `server/entrypoint.sh` sets mtimes at container start and, only when asked, writes an nginx config with `Age`, `Cache-Control` for `index.html`, or the SPA fallback (`try_files $uri /index.html`). Without those, the stock config of `nginx:1.30.5` is used.
- `runner/plan.mjs` the groups (one port and one v1/v2 container pair per group).
- `runner/run.mjs` the run. No request interception is used.
- `scripts/run.sh` one command: build images, install the runner, run, summarize.
- `scripts/summarize.py` markdown summary of `results.json`.
- `results/<run>/` `results.json` (one record per user), `nginx/*.access.log` and `*.error.log` per container, `summary.md`, `durations.txt`, `runner.log`, `images.txt`.

## Sets

| set | A | r | server |
|:---|:---|:---|:---|
| cal-wait | 10m | 5/9/11% by real waiting | stock |
| cal-age | 10m | 5/9/11% by `Age` | stock + `Age` on v1 |
| grid | 10m, 100m, 1000m, 6d | 5/9/11/20% by `Age`; at 100m also the reload method | stock + `Age` on v1 |
| keep | 100m | 5/9/11/20% | v2 image also has the v1 assets |
| spa | 100m | 5/9/11/20% | SPA fallback on v1 and v2 |
| nocache | 100m | 5/9/11/20% | `Cache-Control: no-cache` on `index.html` |
| maxage | 100m; also 10m and 1000m | 300/540/660/840/960/1200 s at 100m; 840/960 s at 10m and 1000m | `Cache-Control: max-age=900` on `index.html` |
| deploytime | 10m | 5/9/11/20% by real waiting; deploy early / mid / late | stock |
| nocache304 | 100m | 5/9/11/20% by `Age`; with and without a deploy | `Cache-Control: no-cache` on `index.html`; access log adds `inm="$http_if_none_match" ims="$http_if_modified_since"` |
| reloadfail | 100m | 5/9/11/20% | app reloads once on `vite:preloadError` |

Three users per point, each with its own persistent profile. After the fetch the browser is closed; the revisit relaunches the same profile, opens `context.newPage()` and navigates (the disk cache is used). The reload method then calls `page.reload()` before pressing the button. Each user sends a user agent ending in `chunk-repro/<user id>` so its access log lines can be picked out.

Deploy timing cannot be expressed with the `Age` header: `Age` puts the whole fetch-to-revisit interval before the fetch, so there is no real time between fetch and revisit to place a deploy in. The `deploytime` set therefore waits in real time. `early` deploys right after the fetches, `mid` at half of the earliest r of the group, `late` 3 s before the earliest revisit. `measured.deployStartFrac` / `deployDoneFrac` record where the deploy actually fell between fetch (0) and revisit (1).

In `nocache304` groups without a deploy, the v1 container also serves the revisit; its access log is split at a snapshot taken after the fetches (`accessLog.v1` = fetch, `accessLog.v2` = revisit).

The real-wait sets (`cal-wait`, `cal-age`, `deploytime`) run first, all groups at once. The other sets follow, `--parallel` groups at a time.

If both calibration sets run and the booted versions differ at any point, the run stops after calibration (exit 2).

## Run

On Linux with Docker, Node 22, and python3:

```
cd chunk-load-heuristic-freshness
scripts/run.sh                                  # everything
scripts/run.sh --sets grid --ages 100m          # one set / one A
scripts/run.sh --sets cal-wait,cal-age          # calibration only
```

Options passed through to `runner/run.mjs`: `--sets`, `--ages`, `--parallel` (groups at once, default 4), `--max-browsers` (default 16), `--base-port` (default 18000), `--no-calibration-gate`.

## Pinned versions

- Playwright 1.63.0 with `chromium-headless-shell` (Chromium 153.0.8010.12)
- `nginx:1.30.5`, `node:22.23.3-bookworm-slim` (build stage), Vite 8.3.1
- `results.json` `env` records the browser version, image IDs, node, kernel, and CPU count of each run.

## Chromium source

At tag `153.0.8010.12` (commit `971a7443b0c9b0a9b2860529b33331b76077ec62`):

- `net/http/http_response_headers.cc`, `HttpResponseHeaders::GetFreshnessLifetimes` (line 1266), line 1350: `lifetimes.freshness = (date_value - last_modified_value.value()) / 10;`
- same file, `HttpResponseHeaders::GetCurrentAge` (line 1416): `corrected_initial_age = max(apparent_age, age_value + response_delay)`, which is where the `Age` header enters.
- same file, `HttpResponseHeaders::RequiresValidation` (line 1187): `VALIDATION_NONE` when `freshness > age`.
- `net/http/http_cache_transaction.cc`, `HttpCache::Transaction::RequiresValidation` (line 3196): `LOAD_VALIDATE_CACHE` forces synchronous validation.
