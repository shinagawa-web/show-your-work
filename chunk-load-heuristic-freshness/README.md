# chunk-load-heuristic-freshness

A Vite app (entry JS plus one chunk loaded when a button is pressed) is served by nginx with no `Cache-Control`. Each user fetches v1 in Chromium, then the v1 container is replaced by a v2 container that no longer has the v1 hashed JS, and the user opens the site again and presses the button. The run records which version boots, whether the chunk loads, Navigation/Resource Timing `transferSize`, console messages, and the nginx access log lines for each user.

## Run

`.github/workflows/chunk-load-heuristic-freshness.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

The workflow runs every set. `summary.md` and `durations.txt` go to the job summary, and `results/<UTC time>/` is uploaded as the `chunk-load-heuristic-freshness-results` artifact.

## Variables

- A: age of `index.html` when it is fetched (`Date` minus `Last-Modified`)
- r: time from fetch to revisit, produced in one of two ways:
  - real wait: the runner waits r seconds before revisiting
  - `Age` header: the v1 container serves `index.html` with `Age: <r>` and the user revisits right after the deploy. The recorded r is the `Age` value plus the seconds that actually passed
- The boundary is judged with the measured `r / A` (`measured.r_over_A` in `results.json`), not the target values

The sets and the A, r and server settings of each are in `runner/plan.mjs`. A deploy between fetch and revisit cannot be placed with the `Age` header, so the `deploytime` set waits in real time.

The real-wait sets, including the calibration sets `cal-wait` and `cal-age`, run first. If a calibration user boots a version that contradicts its measured r/A, the other sets are not run and the run exits with 2.

## Pinned versions

Playwright in `runner/package.json`, the nginx and node images in `server/Dockerfile`, Vite in `app/package.json`. `results.json` `env` records the browser version and image IDs of each run.

## Chromium source

At tag `153.0.8010.12` (commit `971a7443b0c9b0a9b2860529b33331b76077ec62`):

- `net/http/http_response_headers.cc`, `HttpResponseHeaders::GetFreshnessLifetimes` (line 1266), line 1350: `lifetimes.freshness = (date_value - last_modified_value.value()) / 10;`
- same file, `HttpResponseHeaders::GetCurrentAge` (line 1416): `corrected_initial_age = max(apparent_age, age_value + response_delay)`, which is where the `Age` header enters.
- same file, `HttpResponseHeaders::RequiresValidation` (line 1187): `VALIDATION_NONE` when `freshness > age`.
- `net/http/http_cache_transaction.cc`, `HttpCache::Transaction::RequiresValidation` (line 3196): `LOAD_VALIDATE_CACHE` forces synchronous validation.
