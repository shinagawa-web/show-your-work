# cached-301-stale-redirect

nginx serves a docs site whose entrance redirects to `/docs/v3/`. Chromium, WebKit and Firefox (Playwright, persistent profiles) open the entrance from a link on a page of another site. The entrance is then pointed at `/docs/v4/` with `nginx -s reload`, and each browser opens it again right away, after a wait, and after a browser restart. The run records which version each open reaches, whether the entrance request reached nginx, Chromium's `fromDiskCache` for the entrance redirect, and the headers nginx returns before and after the switch.

## Run

`.github/workflows/cached-301-stale-redirect.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab.

`summary.md` and `durations.txt` go to the job summary, and `results/<UTC time>/` is uploaded as the `cached-301-stale-redirect-results` artifact.

## Conditions

Each condition has its own entrance path, defined in `server/docs.conf.template`:

- `b0`: `return 301`, no `Cache-Control`
- `b1`: `return 308`
- `b2`: `return 302`
- `b3`: `return 301` with `Cache-Control: max-age=60`
- `b4`: as `b0`, on its own origin (`localhost:18081`) and its own browser profile. That origin's `/docs/v4/` returns `Clear-Site-Data: "cache"`. After the switch, the browser opens the entrance, then opens `/docs/v4/` in a new tab, then opens the entrance again
- `b5`: as `b0`. After the switch, the entrance is opened with `page.goto` (address bar) instead of the link

The link page is served from `http://127.0.0.1:18080/` and the docs from `http://localhost:18080/`, two different sites. Both are potentially trustworthy origins, which `Clear-Site-Data` requires.

Steps, in `runner/run.mjs`: open every entrance from the link page, switch to v4, open again right away, again once `WAIT_AFTER_SWITCH_SEC` (in `runner/plan.mjs`) has passed since the switch, then close the browser and open again on the same profile.

The `/docs/v3/` and `/docs/v4/` files are served as nginx serves static files by default, with their modification time set 30 days back when the container starts.

After the reload, the run waits until the old nginx worker processes have exited (`worker_shutdown_timeout 1s` in `server/Dockerfile`), so a connection opened before the switch is not served with the old configuration. The run exits with 2 if a first open does not reach v3 through nginx, if nginx returns a v3 `Location` after the switch, or if the wait step starts too early.

## Pinned versions

Playwright in `runner/package.json`, the nginx image in `server/Dockerfile`. `results.json` `env` records the browser versions.
