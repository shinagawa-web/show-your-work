# transient-502-keepalive-reuse

nginx pools keepalive connections to its backend. When the backend closes one and a busy worker reuses it before noticing, the request gets no response: a sporadic 502 that never reproduces by hand.

This reproduces it with nginx in front of two Node.js instances and measures it.

## Run it

On a Linux host with Docker:

```
cd transient-502-keepalive-reuse
docker build -t keepalive-lab .
docker run --rm --cap-add=NET_ADMIN --cap-add=NET_RAW -v "$PWD":/lab keepalive-lab bash /lab/run.sh <scenario>
python3 tools/analyze.py out/<scenario>
```

Output lands in `out/<scenario>/`. Scenario names are in `run.sh`. CI runs `baseline-concurrent`, `baseline-concurrent-noturnover`, `retry-get`, `retry-post`, `nonidem-post`, `ka-timeout` and `drain`.

`tools/analyze.py` reads `loopback.txt` if it exists. To produce it from the capture:

```
docker run --rm -v "$PWD":/lab keepalive-lab bash -c \
  "tcpdump -r /lab/out/<scenario>/loopback.pcap -n -tt > /lab/out/<scenario>/loopback.txt"
```

## Results

Each CI job's summary has the aggregated figures for that scenario. Artifacts carry the raw logs.
