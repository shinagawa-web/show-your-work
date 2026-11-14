for p in /proc/[0-9]*; do
  [ "${p#/proc/}" = "$$" ] && continue
  printf '%s %s ' "${p#/proc/}" "$(cat "$p/comm" 2> /dev/null)"
  grep -E '^(SigPnd|ShdPnd|SigBlk|SigIgn|SigCgt)' "$p/status" 2> /dev/null | tr -s '\t\n' '  '
  echo
done
