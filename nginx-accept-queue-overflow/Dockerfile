FROM debian:trixie-20261005-slim
ARG VEGETA=12.13.0
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      iproute2 procps tcpdump python3 util-linux curl ca-certificates \
 && arch=$(dpkg --print-architecture) \
 && curl -fsSL "https://github.com/tsenart/vegeta/releases/download/v${VEGETA}/vegeta_${VEGETA}_linux_${arch}.tar.gz" | tar -xz -C /usr/local/bin vegeta \
 && rm -rf /var/lib/apt/lists/*
