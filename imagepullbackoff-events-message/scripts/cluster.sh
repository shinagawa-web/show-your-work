#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
. "$here/scripts/versions.env"
export PATH=$here/.bin:$PATH
name=$1
config=$2
work=$3
reg_name=kind-registry
reg_port=5001
mkdir -p "$work/auth" "$work/kind-docker-config" "$work/crane-config"
{
  echo "\$ env | grep -i proxy (host, before cluster creation)"
  env | grep -i proxy || echo "(none)"
  echo "\$ docker info proxy settings"
  docker info --format 'HTTPProxy={{.HTTPProxy}} HTTPSProxy={{.HTTPSProxy}} NoProxy={{.NoProxy}}'
  echo "\$ ~/.docker/config.json proxies"
  jq '.proxies // "(none)"' ~/.docker/config.json 2>/dev/null || echo "(no ~/.docker/config.json)"
} > "$work/host-proxy.txt" 2>&1
unset http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY all_proxy ALL_PROXY
docker rm -f "$reg_name" >/dev/null 2>&1 || true
kind delete cluster --name "$name" >/dev/null 2>&1 || true
htpasswd -Bbn "$REG_USER" "$REG_PASSWORD" > "$work/auth/htpasswd"
docker run -d --restart=always -p "127.0.0.1:${reg_port}:5000" --network bridge --name "$reg_name" \
  -v "$work/auth:/auth:ro" \
  -e REGISTRY_AUTH=htpasswd -e "REGISTRY_AUTH_HTPASSWD_REALM=Registry Realm" -e REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd \
  "$REGISTRY_IMAGE" >/dev/null
DOCKER_CONFIG=$work/kind-docker-config kind create cluster --name "$name" --image "$KIND_NODE_IMAGE" --config "$config" --wait 120s >&2
dir="/etc/containerd/certs.d/localhost:${reg_port}"
for node in $(kind get nodes --name "$name"); do
  docker exec "$node" mkdir -p "$dir"
  printf '[host."http://%s:5000"]\n' "$reg_name" | docker exec -i "$node" cp /dev/stdin "$dir/hosts.toml"
done
if [ "$(docker inspect -f='{{json .NetworkSettings.Networks.kind}}' "$reg_name")" = 'null' ]; then
  docker network connect kind "$reg_name"
fi
kubectl apply -f - >/dev/null <<YAML
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:${reg_port}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
YAML
for i in $(seq 30); do curl -fs -o /dev/null -u "$REG_USER:$REG_PASSWORD" "http://127.0.0.1:${reg_port}/v2/" && break; sleep 1; done
export DOCKER_CONFIG=$work/crane-config
crane auth login "localhost:${reg_port}" -u "$REG_USER" -p "$REG_PASSWORD" >&2
