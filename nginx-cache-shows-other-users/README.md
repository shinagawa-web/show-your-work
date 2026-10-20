# nginx-cache-shows-other-users

nginx caches item pages with `proxy_cache` in front of a minimal application server. The application server puts the logged-in user's name, or a "Log in" link, at the top of each item page, and sends no `Set-Cookie`, `Cache-Control` or `Vary` on those pages unless a condition says so. k6 sends requests at a constant rate, some with the session cookie of a logged-in user and the rest without a cookie, and records whose page each response was rendered for. The run also records the nginx access log with `$upstream_cache_status`, the requests that reached the application server, the number of cache files at the end, the effective nginx configuration, and a curl check. Each condition changes one item from the base or from another named condition.

## Run

`.github/workflows/nginx-cache-shows-other-users.yml` runs it on GitHub Actions, on a push that changes this folder or from Run workflow in the Actions tab. Each job runs one condition with `./run-all.sh <condition>`.

`results/summary.md` goes to the job summary: per condition, the share of responses rendered for another user, the share of logged-in requests that got the logged-out page, requests per second at the application server, cache files at the end and `dropped_iterations`, the checks, the lines changed against the origin condition, and access-log counts by cookie and `$upstream_cache_status`. `results/` is uploaded as the `nginx-cache-shows-other-users-<condition>` artifact. The job fails when a check fails: the run did not finish, k6 dropped iterations or got a response it could not read, the request count does not match the rate, the configuration nginx, the application server or k6 ran with differs from the settings of the condition, more than one item differs from the origin condition, or the first curl request did not get the checker's own page.

## Conditions

- The values every condition shares are in `run-all.sh` (rate, duration, pages, users, and the defaults of the values a condition can change), `nginx/default.conf.template` (loaded by the image's default `nginx.conf`), `app/app.py`, `k6/load.js` and `docker-compose.yml`
- What a condition changes is in `conditions/<condition>.env`. `FROM` names the condition it is compared with
- `results/settings/<condition>/` holds the rendered nginx configuration, its `nginx -T` output, and the settings passed to the application server and k6
- Each page carries `<!-- for:<user> page:<n> -->`. k6 compares the user in it with the user who sent the request; `k6-requests.log` has one line per request
- `scripts/curl-check.sh` runs after the load: it logs in, opens a URL with a unique query string with the session cookie, then without a cookie, and prints the headers, the bodies, the access-log lines and the key and marker of the cache file

## Pinned versions

nginx in `run-all.sh` and `docker-compose.yml`, the application server and k6 images in `docker-compose.yml`. `results/environment.txt` records `nginx -V` and the k6 version.

nginx 1.30.6 source:

- Default cache key: [ngx_http_proxy_module.c#L1096-L1121](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/modules/ngx_http_proxy_module.c#L1096-L1121)
- Responses not cached: `Set-Cookie` [ngx_http_upstream.c#L5065](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_upstream.c#L5065), `Cache-Control` [ngx_http_upstream.c#L5109-L5140](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_upstream.c#L5109-L5140), `proxy_ignore_headers` values [ngx_http_upstream.c#L489-L500](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_upstream.c#L489-L500)
- `Vary`: [ngx_http_upstream.c#L5507-L5580](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_upstream.c#L5507-L5580), [ngx_http_file_cache.c#L1125](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_file_cache.c#L1125)
- Cache lookup skipped only with `proxy_cache_bypass`: [ngx_http_upstream.c#L910-L917](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_upstream.c#L910-L917); the response is still stored unless `proxy_no_cache`: [ngx_http_upstream.c#L3386-L3406](https://github.com/nginx/nginx/blob/release-1.30.6/src/http/ngx_http_upstream.c#L3386-L3406)
