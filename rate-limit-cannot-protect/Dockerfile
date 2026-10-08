FROM golang:1.27.1-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY app ./app
COPY sampler ./sampler
RUN CGO_ENABLED=0 go build -trimpath -o /out/app ./app \
 && CGO_ENABLED=0 go build -trimpath -o /out/sampler ./sampler

FROM scratch AS app
COPY --from=build /out/app /app

FROM ubuntu:24.04 AS probe
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 bpftrace \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/sampler /sampler
