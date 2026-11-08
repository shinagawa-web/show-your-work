import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SERVICES = ["envoy", "a", "b", "c"]


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def search(jaeger, out):
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    params = {
        "query.serviceName": "",
        "query.startTimeMin": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "query.startTimeMax": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "query.searchDepth": "1000",
        "query.rawTraces": "true",
    }
    for service in SERVICES:
        params["query.serviceName"] = service
        status, body = get(f"http://{jaeger}/api/v3/traces?{urllib.parse.urlencode(params)}")
        (out / f"service-{service}.json").write_text(json.dumps({"status": status, "body": body}))


def trace(jaeger, trace_id, path):
    status, body = get(f"http://{jaeger}/api/v3/traces/{trace_id}?rawTraces=true")
    pathlib.Path(path).write_text(json.dumps({"status": status, "body": body}))


if sys.argv[1] == "search":
    search(sys.argv[2], pathlib.Path(sys.argv[3]))
else:
    trace(sys.argv[2], sys.argv[3], sys.argv[4])
