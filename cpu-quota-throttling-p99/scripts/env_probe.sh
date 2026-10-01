#!/usr/bin/env bash
set -euo pipefail

cg_of_pid() { echo "/sys/fs/cgroup$(sed -n 's/^0:://p' "/proc/$1/cgroup")"; }
stat_keys() { grep -E '^(nr_periods|nr_throttled|throttled_usec) ' "$1" | tr '\n' ' '; }

emit() {
  python3 - "$@" <<'EOF'
import json, sys
it = iter(sys.argv[1:])
print(json.dumps(dict(zip(it, it))))
EOF
}

probe_docker() {
  docker rm -f cqt-env >/dev/null 2>&1 || true
  docker run -d --name cqt-env --cpus 0.5 alpine:3.19 sleep 600 >/dev/null
  pid=$(docker inspect cqt-env --format '{{.State.Pid}}')
  cg=$(cg_of_pid "$pid")
  host_max=$(cat "$cg/cpu.max"); host_stat=$(stat_keys "$cg/cpu.stat")
  in_max=$(docker exec cqt-env cat /sys/fs/cgroup/cpu.max)
  in_stat=$(docker exec cqt-env cat /sys/fs/cgroup/cpu.stat | grep -E '^(nr_periods|nr_throttled|throttled_usec) ' | tr '\n' ' ')
  in_cgroup=$(docker exec cqt-env cat /proc/1/cgroup)
  host_stat_after=$(stat_keys "$cg/cpu.stat")
  docker rm -f cqt-env >/dev/null
  emit env docker limit_setting "docker run --cpus 0.5" host_cgroup "$cg" host_cpu.max "$host_max" host_cpu.stat "$host_stat" host_cpu.stat_after_exec "$host_stat_after" \
    inside_path /sys/fs/cgroup inside_proc_1_cgroup "$in_cgroup" inside_cpu.max "$in_max" inside_cpu.stat "$in_stat" \
    cpu.max_same "$([ "$host_max" = "$in_max" ] && echo yes || echo no)" cpu.stat_same "$([ "$host_stat_after" = "$in_stat" ] && echo yes || echo no)"
}

probe_systemd() {
  systemctl stop cqt-quota-svc.service >/dev/null 2>&1 || true
  systemd-run --quiet --unit cqt-quota-svc -p CPUQuota=50% sleep 600
  sleep 0.5
  pid=$(systemctl show -p MainPID --value cqt-quota-svc.service)
  cg=$(cg_of_pid "$pid")
  svc=(env systemd-service limit_setting "systemd-run -p CPUQuota=50% (transient .service)" host_cgroup "$cg" host_cpu.max "$(cat "$cg/cpu.max")" host_cpu.stat "$(stat_keys "$cg/cpu.stat")" \
    unit_CPUQuotaPerSecUSec "$(systemctl show -p CPUQuotaPerSecUSec --value cqt-quota-svc.service)")
  systemctl stop cqt-quota-svc.service
  emit "${svc[@]}"

  systemd-run --quiet --scope --unit cqt-quota-scope -p CPUQuota=50% sleep 601 >/dev/null 2>&1 &
  trap 'systemctl stop cqt-quota-scope.scope >/dev/null 2>&1 || true' EXIT
  sleep 1
  pid=$(pgrep -f 'sleep 601$')
  cg=$(cg_of_pid "$pid")
  sc=(env systemd-scope limit_setting "systemd-run --scope -p CPUQuota=50%" host_cgroup "$cg" host_cpu.max "$(cat "$cg/cpu.max")" host_cpu.stat "$(stat_keys "$cg/cpu.stat")")
  systemctl stop cqt-quota-scope.scope
  trap - EXIT
  wait || true
  emit "${sc[@]}"
}

probe_k3s() {
  k=(k3s kubectl)
  "${k[@]}" delete pod cqt-env --ignore-not-found --wait=true >/dev/null
  "${k[@]}" apply -f - >/dev/null <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: cqt-env
spec:
  containers:
  - name: c
    image: alpine:3.19
    command: ["sleep", "600"]
    resources:
      limits:
        cpu: 500m
EOF
  "${k[@]}" wait --for=condition=Ready pod/cqt-env --timeout=180s >/dev/null
  cid=$("${k[@]}" get pod cqt-env -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's#.*://##')
  pid=$(k3s crictl inspect --output go-template --template '{{.info.pid}}' "$cid")
  cg=$(cg_of_pid "$pid")
  pod_cg=$(dirname "$cg")
  host_max=$(cat "$cg/cpu.max"); host_stat=$(stat_keys "$cg/cpu.stat")
  in_max=$("${k[@]}" exec cqt-env -- cat /sys/fs/cgroup/cpu.max)
  in_stat=$("${k[@]}" exec cqt-env -- cat /sys/fs/cgroup/cpu.stat | grep -E '^(nr_periods|nr_throttled|throttled_usec) ' | tr '\n' ' ')
  in_cgroup=$("${k[@]}" exec cqt-env -- cat /proc/1/cgroup)
  host_stat_after=$(stat_keys "$cg/cpu.stat")
  pod_max=$(cat "$pod_cg/cpu.max"); pod_stat=$(stat_keys "$pod_cg/cpu.stat")
  qos=$("${k[@]}" get pod cqt-env -o jsonpath='{.status.qosClass}')
  "${k[@]}" delete pod cqt-env --wait=true >/dev/null
  emit env kubernetes-k3s limit_setting "Pod resources.limits.cpu: 500m" k3s_version "$(k3s --version | head -1)" qos_class "$qos" \
    host_cgroup "$cg" host_cpu.max "$host_max" host_cpu.stat "$host_stat" host_cpu.stat_after_exec "$host_stat_after" \
    pod_cgroup "$pod_cg" pod_cpu.max "$pod_max" pod_cpu.stat "$pod_stat" \
    inside_path /sys/fs/cgroup inside_proc_1_cgroup "$in_cgroup" inside_cpu.max "$in_max" inside_cpu.stat "$in_stat" \
    cpu.max_same "$([ "$host_max" = "$in_max" ] && echo yes || echo no)" cpu.stat_same "$([ "$host_stat_after" = "$in_stat" ] && echo yes || echo no)"
}

k3s_install() {
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --disable servicelb --disable metrics-server" sh - >/dev/null
  for _ in $(seq 90); do k3s kubectl get nodes 2>/dev/null | grep -q ' Ready' && break; sleep 2; done
  for _ in $(seq 90); do k3s kubectl get sa default >/dev/null 2>&1 && break; sleep 2; done
}

k3s_uninstall() {
  if [ -x /usr/local/bin/k3s-uninstall.sh ]; then /usr/local/bin/k3s-uninstall.sh >/dev/null 2>&1; fi
  systemctl stop kubepods.slice >/dev/null 2>&1 || true
  rm -rf /etc/rancher /var/lib/rancher
}

state() {
  emit check "$1" \
    docker_ps_a "$(docker ps -a --format '{{.Names}}' | tr '\n' ' ')" \
    docker_active "$(systemctl is-active docker)" \
    k3s_processes "$(ps -eo pid=,comm=,args= | awk '$2 !~ /^(bash|sudo|sshd|ps|awk|sh)$/ && /k3s|rancher/' | tr '\n' ';')" \
    k3s_units "$(systemctl list-units --all --no-legend '*k3s*' | tr '\n' ';')" \
    k3s_unit_files "$(ls /etc/systemd/system/ | grep -i k3s | tr '\n' ' ' || true)" \
    k3s_binaries "$(ls /usr/local/bin/ | grep -Ei 'k3s|kubectl|crictl|ctr' | tr '\n' ' ' || true)" \
    kubepods_cgroup "$(ls /sys/fs/cgroup | grep -i kube | tr '\n' ' ' || true)" \
    kubepods_units "$(systemctl list-units --all --no-legend 'kubepods*' | tr '\n' ';')" \
    cni_links "$(ip -o link | grep -Ei 'cni|flannel|veth' | tr '\n' ';' || true)" \
    iptables_kube_rules "$(iptables-save | grep -ciE 'kube|cni|flannel' || true)" \
    rancher_dirs "$(ls -d /var/lib/rancher /etc/rancher 2>/dev/null | tr '\n' ' ' || true)"
}

case "$1" in
  docker) probe_docker ;;
  systemd) probe_systemd ;;
  k3s) k3s_install; probe_k3s ;;
  k3s-uninstall) k3s_uninstall ;;
  state) state "$2" ;;
esac
