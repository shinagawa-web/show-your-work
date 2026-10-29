FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends fio && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["fio"]
