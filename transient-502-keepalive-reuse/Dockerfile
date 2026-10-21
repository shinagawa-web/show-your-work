FROM node:24.22.0-trixie-slim AS node
FROM nginx:1.30.6-trixie
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 tcpdump \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /lab
