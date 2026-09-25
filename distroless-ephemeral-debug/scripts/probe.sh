set -o pipefail
P=$(pgrep -xo server || true)
k() {
  echo "=== $1"
  echo "\$ $2"
  bash -o pipefail -c "$2" 2>&1
  echo "[exit $?]"
}
echo "=== target_pid"
echo "P=$P"
k id 'id'
k caps "grep -E '^(CapPrm|CapEff|CapBnd|NoNewPrivs|Seccomp):' /proc/self/status"
k apparmor "cat /proc/self/attr/current; echo; cat /proc/$P/attr/current; echo"
k ps 'ps auxf'
k ss_tanp 'ss -tanp'
k ss_tanp_proc "ss -tanp | grep 'pid=$P,'"
k ss_lt 'ss -lt'
k curl_health 'curl -sv -m 3 localhost:3000/health'
k tcpdump '(for i in 1 2 3; do sleep 1; curl -s -m 2 localhost:3000/health >/dev/null; done) & tcpdump -i any -nn -c 2 port 3000; rc=$?; wait; exit $rc'
k iptables 'iptables -S'
k dig "dig @\$(awk '/^nameserver/{print \$2; exit}' /etc/resolv.conf) app.default.svc.cluster.local +short"
k status "grep -E '^(Name|State|Uid|Threads|VmRSS):' /proc/$P/status"
k stack "cat /proc/$P/stack"
k fd "ls -l /proc/$P/fd"
k fd_readlink "readlink /proc/$P/fd/0"
k maps "head -n 5 /proc/$P/maps"
k wchan "cat /proc/$P/wchan; echo"
k root_ls "ls /proc/$P/root/"
k root_config "cat /proc/$P/root/config/app.json"
k root_resolv "cat /proc/$P/root/etc/resolv.conf"
k environ "tr '\0' '\n' < /proc/$P/environ"
k cmdline "tr '\0' ' ' < /proc/$P/cmdline; echo"
k limits "grep 'open files' /proc/$P/limits"
k own_cgroup 'cat /proc/self/cgroup; ls /sys/fs/cgroup | head -n 8; cat /sys/fs/cgroup/cpu.max'
k target_cgroup "cat /proc/$P/cgroup"
k target_cgroup_path "ls -d /sys/fs/cgroup\$(cut -d: -f3 /proc/$P/cgroup)"
k root_cpumax "cat /proc/$P/root/sys/fs/cgroup/cpu.max"
k root_cpustat "cat /proc/$P/root/sys/fs/cgroup/cpu.stat"
k write_root "touch /proc/$P/root/probe-write"
k write_scratch "touch /proc/$P/root/scratch/probe-write && ls -ln /proc/$P/root/scratch/"
