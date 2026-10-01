#!/usr/bin/env bash
set -euo pipefail
usage() {
  cat >&2 <<'EOF'
usage: ./run-all.sh [target ...]

targets (default: s3 s4 s5 s6 s9 s10):
  s3              section 3: repro (--cpus 0.5, W=4, Poisson RPS 10)
  s4, s6          sections 4 and 6: read results/s3 (runs s3 first if it is missing)
  s5[:name]       section 5: all conditions, or one (rps5_w4, rps20_w4, rps10_w1)
  s9[:name]       section 9: all conditions, or one (cpus0.75_w4, cpus1.0_w4, cpus1.5_w4, cpus2.0_w4)
  s10             section 10: where the CPU limit appears (Docker, systemd; k3s with K3S=1)

env:
  DUR=60          seconds of load per condition
  K3S=0           1 installs k3s in s10, checks one Pod and uninstalls it
  RESULTS=results output directory

The run ends by writing $RESULTS/summary.md from everything found in $RESULTS.
EOF
}
here=$(cd "$(dirname "$0")" && pwd)
results=${RESULTS:-$here/results}
dur=${DUR:-60}
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
targets=("$@")
[ ${#targets[@]} -gt 0 ] || targets=(s3 s4 s5 s6 s9 s10)

conds() { awk -F'\t' -v s="$1" 'NR > 1 && $1 == s { print $2, $3, $4, $5 }' "$here/conditions.tsv"; }

for t in "${targets[@]}"; do
  sec=${t%%:*}
  case "$sec" in s3|s4|s5|s6|s9|s10) ;; *) echo "unknown target: $t" >&2; usage; exit 2 ;; esac
  if [ "$t" != "$sec" ] && ! conds "$sec" | awk -v n="${t#*:}" '$1 == n { f = 1 } END { exit !f }'; then
    echo "unknown condition: $t" >&2
    exit 2
  fi
done

mkdir -p "$results/environment"
label=$(printf '%s_' "${targets[@]}" | tr ':' '-')
bpf=$("$here/scripts/preflight.sh" "$results/environment/$(hostname)_${label%_}.txt")
export CQT_BPFTRACE=$bpf

run_cond() {
  local sec=$1 name=$2 cpus=$3 w=$4 rps=$5 out
  out=$results/$sec
  [ "$sec" = s3 ] || out=$results/$sec/$name
  echo "== $sec $name: --cpus $cpus, W=$w, Poisson RPS $rps, ${dur}s" >&2
  "$here/scripts/run.sh" "$out" "$cpus" "$w" "$rps" poisson "$dur"
  python3 "$here/scripts/analyze.py" "$out"
}

run_section() {
  local sec=$1 only=${2:-} name cpus w rps found=0
  while read -r -u 3 name cpus w rps; do
    [ -z "$only" ] || [ "$only" = "$name" ] || continue
    found=1
    run_cond "$sec" "$name" "$cpus" "$w" "$rps"
  done 3< <(conds "$sec")
  [ "$found" = 1 ]
}

s3_done=0
ensure_s3() {
  if [ "$s3_done" = 0 ] && [ ! -f "$results/s3/summary.json" ]; then
    run_section s3
    s3_done=1
  fi
}

for t in "${targets[@]}"; do
  sec=${t%%:*}
  only=
  [ "$t" = "$sec" ] || only=${t#*:}
  case "$sec" in
    s3) run_section s3; s3_done=1 ;;
    s4|s6) ensure_s3 ;;
    s5|s9) run_section "$sec" "$only" ;;
    s10)
      echo "== s10: where the CPU limit appears" >&2
      out=$results/s10
      mkdir -p "$out"
      probe() { sudo -n bash "$here/scripts/env_probe.sh" "$@"; }
      : > "$out/env.jsonl"
      probe state before >> "$out/env.jsonl"
      probe docker >> "$out/env.jsonl"
      probe systemd >> "$out/env.jsonl"
      if [ "${K3S:-0}" = 1 ]; then
        trap 'probe k3s-uninstall || true' EXIT
        probe k3s >> "$out/env.jsonl"
        probe k3s-uninstall
        trap - EXIT
        probe state after_k3s_uninstall >> "$out/env.jsonl"
      fi
      ;;
  esac
done

python3 "$here/scripts/report.py" summary "$results" > "$results/summary.md"
cat "$results/summary.md"
echo "wrote $results/summary.md" >&2
