while :; do
  t=$(date +%s.%N)
  ps -eo pid=,ppid=,comm= | awk '{c[$1]=$3; p[$1]=$2; n[$1]=$3} END {for (i in n) if (n[i] == "gunicorn" && c[p[i]] ~ /^containerd-shim/) print i}' | while read -r pid; do
    pod=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | sed -n 's/^HOSTNAME=//p')
    nsenter -t "$pid" -n ss -Hltn 2>/dev/null | awk -v t="$t" -v pod="$pod" -v pid="$pid" '{print t, pod, pid, $2, $3, $4}'
    awk -v t="$t" -v pod="$pod" -v pid="$pid" '/^TcpExt:/ {if (!h) {for (i = 2; i <= NF; i++) k[i] = $i; h = 1} else for (i = 2; i <= NF; i++) {if (k[i] == "ListenOverflows") lo = $i; if (k[i] == "ListenDrops") ld = $i}} END {if (h) print t, pod, pid, "NETSTAT", lo, ld}' /proc/$pid/net/netstat 2>/dev/null
  done
  sleep 1
done
