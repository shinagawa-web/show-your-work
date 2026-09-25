set -o pipefail
P=$(pgrep -xo server)
k() {
  echo "=== $1"
  echo "\$ $2"
  bash -o pipefail -c "$2" 2>&1
  echo "[exit $?]"
}
echo "### A: no response"
k a_curl_hang 'curl -sv -m 3 localhost:3000/hang'
k a_curl_health 'curl -sv -m 3 localhost:3000/health'
k a_svc_hang 'curl -sv -m 3 app.default.svc.cluster.local:3000/hang'
k a_svc_health 'curl -sv -m 3 app.default.svc.cluster.local:3000/health'
k a_misrouted_health 'curl -sv -m 3 app-misrouted.default.svc.cluster.local:3000/health'
echo "=== a_fill"
echo '$ for i in $(seq 10); do curl -s -m 15 localhost:3001 >/dev/null 2>&1 & done'
for i in $(seq 10); do curl -s -m 15 localhost:3001 >/dev/null 2>&1 & done
sleep 2
k a_ss_lt 'ss -lt'
k a_ss_3001 "ss -tan '( sport = :3001 or dport = :3001 )'"
k a_stack "cat /proc/$P/stack"
k a_wchan "cat /proc/$P/wchan; echo"
k a_task_wchan "for t in /proc/$P/task/*; do printf '%s %s\n' \"\${t##*/}\" \"\$(cat \$t/wchan)\"; done"
echo "### B: fd leak"
k b_fd_count "ls /proc/$P/fd | wc -l"
k b_limits "cat /proc/$P/limits"
k b_leak 'curl -s localhost:3000/leak?n=100'
k b_fd_count_after "ls /proc/$P/fd | wc -l"
k b_fd_kinds "ls -l /proc/$P/fd | awk 'NR>1{print \$NF}' | sed -E 's/[0-9]+/N/g' | sort | uniq -c"
k b_fd_head "ls -l /proc/$P/fd | sed -n 1,12p"
echo "### C: config"
k c_environ "tr '\0' '\n' < /proc/$P/environ"
k c_config "cat /proc/$P/root/config/app.json"
k c_config_ls "ls -la /proc/$P/root/config/"
wait
