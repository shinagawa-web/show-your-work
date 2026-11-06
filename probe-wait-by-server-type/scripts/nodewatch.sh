while :; do
  t=$(date +%s.%N)
  ps -eo pid=,ppid=,comm= | awk '{c[$1]=$3; p[$1]=$2} END {for (i in c) if (c[p[i]] ~ /^containerd-shim/ && c[i] != "pause") print i}' | while read -r pid; do
    pod=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^HOSTNAME=//p')
    case $pod in app-*) ;; *) continue ;; esac
    nsenter -t "$pid" -n ss -Hltn 2>/dev/null | awk -v t="$t" -v pod="$pod" -v pid="$pid" '{print t, pod, pid, $2, $3, $4}'
    awk -v t="$t" -v pod="$pod" -v pid="$pid" '/^TcpExt:/ {if (!h) {for (i = 2; i <= NF; i++) k[i] = $i; h = 1} else for (i = 2; i <= NF; i++) {if (k[i] == "ListenOverflows") lo = $i; if (k[i] == "ListenDrops") ld = $i}} END {if (h) print t, pod, pid, "NETSTAT", lo, ld}' /proc/$pid/net/netstat 2>/dev/null
    cg=$(sed -n 's/^0:://p' /proc/$pid/cgroup 2>/dev/null)
    n=0
    fds=0
    for p in $(cat "/sys/fs/cgroup$cg/cgroup.procs" 2>/dev/null); do
      c=$(ls /proc/$p/fd 2>/dev/null | wc -l)
      fds=$((fds + c))
      n=$((n + 1))
    done
    echo "$t $pod $pid FD $fds $n"
    echo "$t $pod $pid CPU $(sed -n 's/^usage_usec //p' "/sys/fs/cgroup$cg/cpu.stat" 2>/dev/null)"
  done
  echo "$t - - HOSTCPU $(sed -n 's/^cpu  //p' /proc/stat)"
  sleep "$(awk -v t="$t" -v n="$(date +%s.%N)" 'BEGIN {d = 1 - (n - t); if (d < 0.05) d = 0.05; printf "%.3f", d}')"
done
