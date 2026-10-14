ts() { date -u +%H:%M:%S.%N | cut -c1-12; }
r() {
  echo "\$ $*"
  "$@" 2>&1
  echo "[exit $?]"
}
render() { sed -e "s|@NAME@|$2|g" -e "s|@REPLICAS@|${3:-2}|g" -e "s|@BUSYBOX_TAG@|$BUSYBOX_TAG|g" "$here/k8s/$1"; }
app_of() { [ "$1" = c3 ] && echo app-init.yaml || echo app.yaml; }
hpa_of() { case $1 in c2) echo hpa-window120.yaml ;; c10) echo hpa-target80.yaml ;; *) echo hpa.yaml ;; esac; }
spec_replicas() { kubectl get deploy "$1" -o jsonpath='{.spec.replicas}'; }
fields() {
  kubectl get deploy "$1" -o json --show-managed-fields | jq -r '"spec.replicas=\(.spec.replicas)", (.metadata.managedFields[] | "\(.manager)\t\(.operation)\t\(.subresource // "-")\t\(.time)\t\(if .fieldsV1["f:spec"]["f:replicas"] then "owns spec.replicas" else "-" end)")'
}
screen() {
  local title=$1 what
  shift
  echo "## $title $(ts)"
  echo
  for what in "$@"; do
    case $what in
      deploy) echo "\$ kubectl get deploy $name"; kubectl get deploy "$name" 2>&1 ;;
      hpa) echo "\$ kubectl get hpa $name"; kubectl get hpa "$name" 2>&1 ;;
      describe) echo "\$ kubectl describe hpa $name"; kubectl describe hpa "$name" 2>&1 ;;
    esac
    echo
  done
}
manfields() {
  echo "## managedFields $1 $(ts)"
  echo
  echo "\$ kubectl get deploy $name -o yaml --show-managed-fields"
  kubectl get deploy "$name" -o yaml --show-managed-fields 2>&1
  echo
}
