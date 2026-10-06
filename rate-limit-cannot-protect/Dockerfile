# app:   the downstream service (standard library only), from scratch
# probe: kernel-side sampler (ss / nstat) and bpftrace, on Ubuntu 24.04
FROM golang:1.27.1-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY downstream ./downstream
COPY sampler ./sampler
RUN CGO_ENABLED=0 go build -trimpath -o /out/downstream ./downstream \
 && CGO_ENABLED=0 go build -trimpath -o /out/sampler ./sampler

FROM scratch AS app
COPY --from=build /out/downstream /downstream

FROM ubuntu:24.04 AS probe
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 bpftrace \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/sampler /sampler
