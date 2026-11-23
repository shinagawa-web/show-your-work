#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=$here/results
state=$here/state
bin=$here/.bin
TERRAFORM=1.17.1
KUBECTL=1.36.6
HELM=4.3.1
KO=v0.19.1
ESO_CHART=2.14.0
RELOADER_CHART=2.2.19
ROTATION_APP=arn:aws:serverlessrepo:us-east-1:297356227824:applications/SecretsManagerRDSMySQLRotationSingleUser
ROTATION_VERSION=1.1.749
MYSQL_IMAGE=public.ecr.aws/docker/library/mysql:8.4.11
LATE_AFTER=960
NAME=eks-rds-iam-auth
SECRET=$NAME/app-db
ROTATION_STACK=serverlessrepo-$NAME-rotation

export AWS_REGION=${AWS_REGION:-ap-northeast-1}
export AWS_PAGER=
export KUBECONFIG=$state/kubeconfig
export DOCKER_CONFIG=$state/docker
export PATH=$bin:$PATH
export TF_IN_AUTOMATION=1

account=$(aws sts get-caller-identity --query Account --output text)

mask() {
  sed -e "s/$account/111122223333/g"
}

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

iso() {
  date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ
}

epoch() {
  jq -rn --arg t "$1" '$t | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601'
}

install_tools() {
  mkdir -p "$bin"
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  case $(uname -m) in
    x86_64 | amd64) arch=amd64 ;;
    *) arch=arm64 ;;
  esac
  if [[ $("$bin/terraform" version -json 2>/dev/null | jq -r .terraform_version) != "$TERRAFORM" ]]; then
    curl -fsSLo "$bin/terraform.zip" "https://releases.hashicorp.com/terraform/$TERRAFORM/terraform_${TERRAFORM}_${os}_${arch}.zip"
    unzip -oq "$bin/terraform.zip" terraform -d "$bin"
    rm "$bin/terraform.zip"
  fi
  if [[ $("$bin/kubectl" version --client -o json 2>/dev/null | jq -r .clientVersion.gitVersion) != "v$KUBECTL" ]]; then
    curl -fsSLo "$bin/kubectl" "https://dl.k8s.io/release/v$KUBECTL/bin/$os/$arch/kubectl"
    chmod +x "$bin/kubectl"
  fi
  if [[ $("$bin/helm" version --template '{{.Version}}' 2>/dev/null) != "v$HELM" ]]; then
    curl -fsSL "https://get.helm.sh/helm-v$HELM-$os-$arch.tar.gz" | tar -xzf - -C "$bin" --strip-components 1 "$os-$arch/helm"
  fi
  if [[ ! -x $bin/ko-$KO ]]; then
    GOBIN=$bin go install "github.com/google/ko@$KO"
    mv "$bin/ko" "$bin/ko-$KO"
  fi
}

tf() {
  local root=$1
  shift
  mkdir -p "$state/tf/$root"
  TF_DATA_DIR=$state/tf/$root/.terraform terraform -chdir="$here/terraform/$root" "$@"
}

tf_init() {
  tf "$1" init -input=false -no-color -backend-config="path=$state/tf/$1/terraform.tfstate" >/dev/null
}

tf_apply() {
  local root=$1
  shift
  tf_init "$root"
  tf "$root" apply -auto-approve -input=false -no-color -var "region=$AWS_REGION" "$@"
}

tf_destroy() {
  local root=$1
  shift
  [[ -f $state/tf/$root/terraform.tfstate ]] || return 0
  tf_init "$root"
  tf "$root" destroy -auto-approve -input=false -no-color -var "region=$AWS_REGION" "$@"
}

out() {
  tf up output -raw "$1"
}

context() {
  cluster=$(out cluster_name)
  db_host=$(out db_address)
  db_resource_id=$(out db_resource_id)
  registry=$(out registry)
  dir=$results/$current
}

image() {
  local ref
  aws ecr get-login-password | "$bin/ko-$KO" login "$registry" -u AWS --password-stdin >/dev/null 2>&1
  ref=$(cd "$here/app" && KO_DOCKER_REPO=$registry/$NAME "$bin/ko-$KO" build --base-import-paths --platform=linux/arm64 "./$1" 2>>"$dir/ko.log")
  echo "$ref"
}

render() {
  local file=$1 kv
  shift
  local args=()
  for kv in "$@"; do
    args+=(-e "s|@${kv%%=*}@|${kv#*=}|g")
  done
  sed "${args[@]}" "$here/k8s/$file"
}

wait_job() {
  local ns=$1 job=$2 deadline=$((SECONDS + ${3:-300})) s
  while ((SECONDS < deadline)); do
    s=$(kubectl -n "$ns" get job "$job" -o json | jq -r '[.status.conditions[]? | select(.status == "True") | .type] | join(",")')
    case $s in
      *Complete*) return 0 ;;
      *Failed*) return 1 ;;
    esac
    sleep 3
  done
  return 1
}

admin() {
  kubectl create namespace dbadmin --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n dbadmin create secret generic db-admin --from-literal=password="$(out db_admin_password)" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

sql() {
  local name=$1 file=$2 rc=0
  admin
  kubectl -n dbadmin delete job "$name" --ignore-not-found --wait=true >/dev/null
  kubectl -n dbadmin create secret generic "$name" --from-file=run.sql="$file" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  render sql-job.yaml NAME="$name" IMAGE="$MYSQL_IMAGE" DB_HOST="$db_host" | kubectl apply -f - >/dev/null
  wait_job dbadmin "$name" 300 || rc=1
  kubectl -n dbadmin logs "job/$name"
  kubectl -n dbadmin delete job "$name" --wait=false >/dev/null
  kubectl -n dbadmin delete secret "$name" >/dev/null
  return "$rc"
}

pods_json() {
  kubectl -n app get pods -l app=app -o json |
    jq -c '[.items[] | select(.metadata.deletionTimestamp == null) | {name: .metadata.name, started: .status.startTime, ready: ([.status.conditions[]? | select(.type == "Ready") | .status] | first == "True"), image: (.spec.containers[0].image | split("@")[0])}]'
}

window_start() {
  date +%s >"$dir/window-start"
}

window_end() {
  date +%s >"$dir/window-end"
  local from to
  from=$(cat "$dir/window-start")
  to=$(cat "$dir/window-end")
  kubectl -n app logs deploy/loadgen --since-time="$(iso "$((from - 5))")" |
    jq -c --argjson from "$from" --argjson to "$to" \
      'select(.t | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 | . >= $from and . < $to)' >"$dir/load.jsonl"
  jq -s --arg stage "$current" --arg from "$(iso "$from")" --arg to "$(iso "$to")" \
    '{stage: $stage, from: $from, to: $to, requests: length, failures: (map(select(.ok | not)) | length)}' "$dir/load.jsonl" >"$dir/load.json"
  cat "$dir/load.json"
}

up() {
  tf_apply up
  aws eks update-kubeconfig --name "$(out cluster_name)" --kubeconfig "$KUBECONFIG" >/dev/null
  kubectl wait --for=condition=Ready node --all --timeout=300s
  kubectl -n kube-system rollout status daemonset/eks-pod-identity-agent --timeout=180s
  kubectl get nodes -o wide
  kubectl -n kube-system get daemonset eks-pod-identity-agent
}

token() {
  context
  tf_apply token -var "cluster_name=$cluster"
  local img
  img=$(image tokencheck)
  kubectl delete namespace tokencheck --ignore-not-found --wait=true
  render tokencheck.yaml IMAGE="$img" DB_HOST="$db_host" | kubectl apply -f -
  local rc=0
  wait_job tokencheck tokencheck 180 || rc=1
  kubectl -n tokencheck logs job/tokencheck | tee "$dir/tokencheck.log"
  return "$rc"
}

s2() {
  context
  tf_apply s2 -var "cluster_name=$cluster"
  local secret_arn pw value
  secret_arn=$(tf s2 output -raw secret_arn)
  if ! aws secretsmanager get-secret-value --secret-id "$SECRET" >/dev/null 2>&1; then
    pw=$(aws secretsmanager get-random-password --exclude-characters '/@"'"'"'\' --password-length 32 \
      --require-each-included-type --query RandomPassword --output text)
    jq -n --arg host "$db_host" --arg password "$pw" \
      '{engine: "mysql", host: $host, port: 3306, username: "app", password: $password, dbname: "appdb"}' >"$state/secret.json"
    aws secretsmanager put-secret-value --secret-id "$SECRET" --secret-string "file://$state/secret.json" --query VersionId --output text
    rm "$state/secret.json"
  fi
  pw=$(aws secretsmanager get-secret-value --secret-id "$SECRET" --query SecretString --output text | jq -r .password)
  cat >"$state/s2.sql" <<EOF
CREATE TABLE IF NOT EXISTS items (id INT PRIMARY KEY, name VARCHAR(64));
INSERT IGNORE INTO items VALUES (1, 'one');
CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED BY '$pw';
ALTER USER 'app'@'%' IDENTIFIED BY '$pw';
GRANT SELECT, INSERT, UPDATE, DELETE ON appdb.* TO 'app'@'%';
SHOW GRANTS FOR 'app'@'%';
EOF
  sql s2-app-user "$state/s2.sql" | tee "$dir/sql.log"
  rm "$state/s2.sql"

  if ! aws cloudformation describe-stacks --stack-name "$ROTATION_STACK" >/dev/null 2>&1; then
    local params cs
    params=$(jq -nc --arg region "$AWS_REGION" --arg subnets "$(tf up output -json subnet_ids | jq -r 'join(",")')" \
      --arg sg "$(out rotation_security_group_id)" --arg role "$(tf s2 output -raw rotation_role_arn)" \
      '[{Name: "endpoint", Value: ("https://secretsmanager." + $region + ".amazonaws.com")},
        {Name: "functionName", Value: "eks-rds-iam-auth-rotation"},
        {Name: "vpcSubnetIds", Value: $subnets},
        {Name: "vpcSecurityGroupIds", Value: $sg},
        {Name: "roleArn", Value: $role}]')
    cs=$(aws serverlessrepo create-cloud-formation-change-set --application-id "$ROTATION_APP" \
      --semantic-version "$ROTATION_VERSION" --stack-name "$NAME-rotation" \
      --capabilities CAPABILITY_IAM CAPABILITY_RESOURCE_POLICY --parameter-overrides "$params" --query ChangeSetId --output text)
    aws cloudformation wait change-set-create-complete --change-set-name "$cs"
    aws cloudformation execute-change-set --change-set-name "$cs"
    aws cloudformation wait stack-create-complete --stack-name "$ROTATION_STACK"
  fi
  local lambda_arn
  lambda_arn=$(aws cloudformation describe-stacks --stack-name "$ROTATION_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='RotationLambdaARN'].OutputValue" --output text)
  if [[ $(aws secretsmanager describe-secret --secret-id "$SECRET" --query RotationEnabled --output text) != True ]]; then
    aws secretsmanager rotate-secret --secret-id "$SECRET" --rotation-lambda-arn "$lambda_arn" \
      --rotation-rules AutomaticallyAfterDays=30 --no-rotate-immediately
  fi

  helm upgrade --install external-secrets external-secrets --repo https://charts.external-secrets.io \
    --version "$ESO_CHART" -n external-secrets --create-namespace --wait --timeout 5m
  helm upgrade --install reloader reloader --repo https://stakater.github.io/stakater-charts \
    --version "$RELOADER_CHART" -n reloader --create-namespace --wait --timeout 5m

  local before loadgen
  before=$(image before)
  loadgen=$(image loadgen)
  kubectl apply -f "$here/k8s/app-base.yaml"
  kubectl -n app delete deployment app --ignore-not-found --cascade=foreground --wait=true
  render eso.yaml REGION="$AWS_REGION" | kubectl apply -f -
  kubectl -n app wait --for=condition=Ready externalsecret/app-db --timeout=120s
  render app-before.yaml IMAGE="$before" | kubectl apply -f -
  kubectl -n app rollout status deployment/app --timeout=300s
  render loadgen.yaml IMAGE="$loadgen" | kubectl apply -f -
  kubectl -n app rollout status deployment/loadgen --timeout=180s

  window_start
  sleep 30
  pods_json >"$dir/pods-before.json"
  jq . "$dir/pods-before.json"
  local old_version old_hash rotate_at rotated_at synced_at restarted_at deadline
  old_version=$(aws secretsmanager describe-secret --secret-id "$SECRET" --output json |
    jq -r '.VersionIdsToStages | to_entries[] | select(.value | index("AWSCURRENT")) | .key')
  old_hash=$(kubectl -n app get secret app-db -o jsonpath='{.data.password}' | shasum | cut -c1-12)
  rotate_at=$(date +%s)
  aws secretsmanager rotate-secret --secret-id "$SECRET"

  deadline=$((SECONDS + 240))
  until [[ $(aws secretsmanager describe-secret --secret-id "$SECRET" --output json |
    jq -r '.VersionIdsToStages | to_entries[] | select(.value | index("AWSCURRENT")) | .key') != "$old_version" ]]; do
    ((SECONDS < deadline)) || { echo 'rotation did not finish'; return 1; }
    sleep 2
  done
  rotated_at=$(date +%s)
  until [[ $(kubectl -n app get secret app-db -o jsonpath='{.data.password}' | shasum | cut -c1-12) != "$old_hash" ]]; do
    ((SECONDS < deadline)) || { echo 'Secret app-db was not updated'; return 1; }
    sleep 1
  done
  synced_at=$(date +%s)
  until pods_json | jq -e --slurpfile old "$dir/pods-before.json" \
    '($old[0] | map(.name)) as $o | (map(select(.ready)) | length) == 2 and all(.[]; (.name as $n | $o | index($n)) == null)' >/dev/null; do
    ((SECONDS < deadline)) || { echo 'pods were not replaced'; return 1; }
    sleep 1
  done
  kubectl -n app rollout status deployment/app --timeout=120s
  restarted_at=$(date +%s)
  sleep 20
  pods_json >"$dir/pods-after.json"
  jq . "$dir/pods-after.json"
  window_end
  jq -n --arg rotate "$(iso "$rotate_at")" --arg rotated "$(iso "$rotated_at")" --arg synced "$(iso "$synced_at")" \
    --arg restarted "$(iso "$restarted_at")" \
    '{rotate_secret_called: $rotate, awscurrent_moved: $rotated, kubernetes_secret_updated: $synced, rollout_finished: $restarted}' |
    tee "$dir/events.json"
  aws logs filter-log-events --log-group-name /aws/lambda/eks-rds-iam-auth-rotation --start-time "$((rotate_at * 1000))" \
    --query 'events[].message' --output json | jq -r '.[]' | mask >"$dir/rotation-lambda.log" || true

  jq -s --slurpfile old "$dir/pods-before.json" --slurpfile new "$dir/pods-after.json" --argjson rotated "$rotated_at" '
    ($old[0] | map(.name)) as $o
    | ($new[0] | map(.name)) as $n
    | map(. + {e: (.t | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)})
    | {
        old_pods_after_rotation: (map(select((.pod as $p | $o | index($p)) != null and .e >= $rotated))
          | {requests: length, failures: (map(select(.ok | not)) | length), conn_ids: (map(.conn_id) | unique)}),
        old_pods_conn_ids_before_rotation: (map(select((.pod as $p | $o | index($p)) != null and .e < $rotated)) | map(.conn_id) | unique),
        failures: map(select(.ok | not)) | map(del(.e)),
        first_new_pod_response: (map(select(.ok and (.pod as $p | $n | index($p)) != null)) | first | del(.e))
      }' "$dir/load.jsonl" | tee "$dir/analysis.json"
  jq -e '.first_new_pod_response.ok' "$dir/analysis.json" >/dev/null
}

s3() {
  context
  window_start
  aws rds modify-db-instance --db-instance-identifier "$(out db_instance_id)" \
    --enable-iam-database-authentication --apply-immediately \
    --query 'DBInstance.[DBInstanceStatus,IAMDatabaseAuthenticationEnabled,PendingModifiedValues]' --output json
  local deadline=$((SECONDS + 600)) st last=
  while :; do
    st=$(aws rds describe-db-instances --db-instance-identifier "$(out db_instance_id)" \
      --query 'DBInstances[0].[DBInstanceStatus,IAMDatabaseAuthenticationEnabled,length(keys(PendingModifiedValues))]' --output text)
    if [[ $st != "$last" ]]; then
      log "status iam_auth pending: $st" | tee -a "$dir/status.log"
      last=$st
    fi
    [[ $st == $'available\tTrue\t0' ]] && break
    ((SECONDS < deadline)) || { echo 'IAM authentication was not enabled'; return 1; }
    sleep 5
  done
  cat >"$state/s3.sql" <<'EOF'
CREATE USER IF NOT EXISTS 'app_iam'@'%' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
GRANT SELECT, INSERT, UPDATE, DELETE ON appdb.* TO 'app_iam'@'%';
SHOW GRANTS FOR 'app'@'%';
SHOW GRANTS FOR 'app_iam'@'%';
SELECT user, host, plugin FROM mysql.user WHERE user IN ('app', 'app_iam');
EOF
  sql s3-iam-user "$state/s3.sql" | tee "$dir/sql.log"
  rm "$state/s3.sql"
  sleep 20
  window_end
}

s4() {
  context
  window_start
  pods_json >"$dir/pods-before.json"
  tf_apply s4 -var "cluster_name=$cluster" -var "connect_resource_id=$db_resource_id"
  tf s4 output -raw policy | jq . | mask | tee "$dir/policy.json"
  local applied=$SECONDS attempts=0
  until kubectl -n app run probe --image=busybox --restart=Never --overrides='{"spec":{"serviceAccountName":"app"}}' \
    --dry-run=server -o json | jq -e '.spec.containers[0].env // [] | map(.name) | index("AWS_CONTAINER_CREDENTIALS_FULL_URI")' >/dev/null; do
    attempts=$((attempts + 1))
    ((SECONDS - applied < 300)) || { echo 'Pod Identity variables were not injected'; return 1; }
    sleep 1
  done
  echo "Pod Identity variables injected into a dry-run Pod $((SECONDS - applied))s after the association, after $attempts dry-runs without them" |
    tee "$dir/injection-wait.log"
  kubectl -n app rollout restart deployment/app
  kubectl -n app rollout status deployment/app --timeout=300s
  sleep 20
  pods_json >"$dir/pods-after.json"
  local p
  for p in $(jq -r '.[].name' "$dir/pods-after.json"); do
    kubectl -n app get pod "$p" -o json |
      jq -c --arg p "$p" '{pod: $p, env: [.spec.containers[0].env[] | select(.name | startswith("AWS_")) | {name, value}]}'
  done | tee "$dir/pod-identity-env.jsonl"
  window_end
  jq -se 'length == 2 and all(.[]; .env | map(.name) | index("AWS_CONTAINER_CREDENTIALS_FULL_URI"))' "$dir/pod-identity-env.jsonl" >/dev/null
}

s5() {
  context
  window_start
  pods_json >"$dir/pods-before.json"
  curl -fsSL -o "$state/rds-ca.pem" "https://truststore.pki.rds.amazonaws.com/$AWS_REGION/$AWS_REGION-bundle.pem"
  kubectl -n app create configmap rds-ca --from-file=bundle.pem="$state/rds-ca.pem" --dry-run=client -o yaml | kubectl apply -f -
  local after
  after=$(image after)
  render app-after.yaml IMAGE="$after" DB_HOST="$db_host" | kubectl apply -f -
  kubectl -n app rollout status deployment/app --timeout=300s
  sleep 20
  pods_json >"$dir/pods-after.json"
  jq . "$dir/pods-after.json"
  window_end
  (cd "$here" && diff -u -L app/before/main.go -L app/after/main.go app/before/main.go app/after/main.go >"$dir/app.diff") || true
  jq -s --slurpfile new "$dir/pods-after.json" '
    ($new[0] | map(.name)) as $n
    | map(select(.ok and (.pod as $p | $n | index($p)) != null)) | first' "$dir/load.jsonl" | tee "$dir/first-new-pod-response.json"
  jq -e '.user == "app_iam@%"' "$dir/first-new-pod-response.json" >/dev/null
}

late_check() {
  local proxy_done=$1 pods p started elapsed token_at t_line
  pods=$(pods_json)
  echo "$pods" >"$dir/late-pods.json"
  p=$(jq -r 'sort_by(.started) | last | .name' <<<"$pods")
  started=$(jq -r 'sort_by(.started) | last | .started' <<<"$pods")
  elapsed=$((proxy_done - $(epoch "$started")))
  if ((elapsed < LATE_AFTER)); then
    jq -n --arg pod "$p" --arg started "$started" --argjson elapsed "$elapsed" --argjson need "$LATE_AFTER" \
      '{checked: false, pod: $pod, started: $started, elapsed_seconds: $elapsed, reason: ("less than " + ($need | tostring) + " seconds after start")}' | tee "$dir/late.json"
    return 0
  fi
  local from deadline
  from=$(date +%s)
  deadline=$((SECONDS + 150))
  while :; do
    token_at=$(kubectl -n app logs "$p" --timestamps --since-time="$(iso "$from")" | grep 'building auth token' | head -1 | cut -d' ' -f1 || true)
    if [[ -n $token_at ]]; then
      t_line=$(kubectl -n app logs deploy/loadgen --since-time="$(iso "$((from - 120))")" |
        jq -sc --arg p "$p" --argjson tok "$(epoch "$token_at")" '
          map(. + {e: (.t | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)}) as $all
          | ($all | map(select(.pod == $p and .ok and .e < $tok)) | map(.conn_id) | max // 0) as $maxold
          | $all | map(select(.pod == $p and .e >= $tok and .ok and .conn_id > $maxold)) | first | del(.e) // empty')
      [[ -n $t_line ]] && break
    fi
    ((SECONDS < deadline)) || { echo 'no new connection was observed'; break; }
    sleep 5
  done
  jq -n --arg pod "$p" --arg started "$started" --arg token_at "$token_at" \
    --argjson token_elapsed "$(($(epoch "${token_at:-$started}") - $(epoch "$started")))" --argjson response "${t_line:-null}" \
    '{checked: true, pod: $pod, started: $started, token_built: $token_at, token_built_seconds_after_start: $token_elapsed, response: $response}' |
    tee "$dir/late.json"
  jq -e '.response.ok' "$dir/late.json" >/dev/null
}

s6() {
  context
  tf_apply s6 -var "vpc_id=$(out vpc_id)" -var "subnet_ids=$(tf up output -json subnet_ids)" \
    -var "cluster_security_group_id=$(out cluster_security_group_id)" -var "rds_security_group_id=$(out rds_security_group_id)" \
    -var "db_instance_id=$(out db_instance_id)" -var "db_resource_id=$db_resource_id"
  local deadline=$((SECONDS + 900)) h
  until [[ $(h=$(aws rds describe-db-proxy-targets --db-proxy-name "$NAME" --query 'Targets[?Type==`RDS_INSTANCE`].TargetHealth.State' --output text); echo "$h") == AVAILABLE ]]; do
    ((SECONDS < deadline)) || { aws rds describe-db-proxy-targets --db-proxy-name "$NAME" --output json; return 1; }
    sleep 10
  done
  aws rds describe-db-proxy-targets --db-proxy-name "$NAME" --query 'Targets[].{Type:Type,Endpoint:Endpoint,Health:TargetHealth}' --output json
  local proxy_done
  proxy_done=$(date +%s)
  late_check "$proxy_done" || true

  local proxy_endpoint proxy_id
  proxy_endpoint=$(tf s6 output -raw proxy_endpoint)
  proxy_id=$(tf s6 output -raw proxy_resource_id)
  window_start
  pods_json >"$dir/pods-before.json"
  tf_apply s4 -var "cluster_name=$cluster" -var "connect_resource_id=$proxy_id"
  tf s4 output -raw policy | jq . | mask | tee "$dir/policy.json"
  curl -fsSL -o "$state/rds-ca.pem" "https://truststore.pki.rds.amazonaws.com/$AWS_REGION/$AWS_REGION-bundle.pem"
  curl -fsSL -o "$state/amazon-root-ca-1.pem" https://www.amazontrust.com/repository/AmazonRootCA1.pem
  cat "$state/rds-ca.pem" "$state/amazon-root-ca-1.pem" >"$state/rds-proxy-ca.pem"
  kubectl -n app create configmap rds-ca --from-file=bundle.pem="$state/rds-proxy-ca.pem" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n app set env deployment/app DB_HOST="$proxy_endpoint"
  local rc=0
  kubectl -n app rollout status deployment/app --timeout=180s || rc=1
  sleep 20
  pods_json >"$dir/pods-after.json"
  jq . "$dir/pods-after.json"
  window_end
  for p in $(jq -r '.[] | select(.ready | not) | .name' "$dir/pods-after.json"); do
    kubectl -n app logs "$p" --tail=20
  done
  jq -s --slurpfile old "$dir/pods-before.json" --slurpfile new "$dir/pods-after.json" --arg host "$proxy_endpoint" '
    ($old[0] | map(.name)) as $o
    | ($new[0] | map(.name) | map(select(. as $p | $o | index($p) == null))) as $n
    | {db_host: $host, first_new_pod_response: (map(select(.ok and (.pod as $p | $n | index($p)) != null)) | first)}' "$dir/load.jsonl" |
    tee "$dir/proxy.json"
  [[ $rc == 0 ]] && jq -e '.first_new_pod_response.ok' "$dir/proxy.json" >/dev/null
}

reset() {
  context
  kubectl -n app delete deployment app loadgen --ignore-not-found --cascade=foreground --wait=true
  tf_destroy s6 -var vpc_id=x -var 'subnet_ids=[]' -var cluster_security_group_id=x -var rds_security_group_id=x \
    -var db_instance_id=x -var db_resource_id=x
  tf_destroy s4 -var "cluster_name=$cluster" -var connect_resource_id=x
  cat >"$state/reset.sql" <<'EOF'
DROP USER IF EXISTS 'app_iam'@'%';
EOF
  sql reset-iam-user "$state/reset.sql"
  rm "$state/reset.sql"
  aws rds modify-db-instance --db-instance-identifier "$(out db_instance_id)" \
    --no-enable-iam-database-authentication --apply-immediately --query 'DBInstance.DBInstanceStatus' --output text
  local deadline=$((SECONDS + 600))
  until [[ $(aws rds describe-db-instances --db-instance-identifier "$(out db_instance_id)" \
    --query 'DBInstances[0].[DBInstanceStatus,IAMDatabaseAuthenticationEnabled,length(keys(PendingModifiedValues))]' --output text) == $'available\tFalse\t0' ]]; do
    ((SECONDS < deadline)) || return 1
    sleep 5
  done
}

down() {
  context
  kubectl delete namespace app tokencheck dbadmin --ignore-not-found --wait=true || true
  helm uninstall reloader -n reloader || true
  helm uninstall external-secrets -n external-secrets || true
  tf_destroy s6 -var vpc_id=x -var 'subnet_ids=[]' -var cluster_security_group_id=x -var rds_security_group_id=x \
    -var db_instance_id=x -var db_resource_id=x
  tf_destroy s4 -var "cluster_name=$cluster" -var connect_resource_id=x
  aws cloudformation delete-stack --stack-name "$ROTATION_STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$ROTATION_STACK"
  tf_destroy s2 -var "cluster_name=$cluster"
  tf_destroy token -var "cluster_name=$cluster"
  tf_destroy up
}

stage() {
  current=$1
  local start=$SECONDS rc
  mkdir -p "$results/$current"
  find "$results/$current" -mindepth 1 -delete
  log "start $current" | tee "$results/$current/run.log"
  (
    set -euo pipefail
    "$current"
  ) 2>&1 | mask | tee -a "$results/$current/run.log"
  rc=${PIPESTATUS[0]}
  echo "$((SECONDS - start))" >"$results/$current/seconds"
  log "end $current rc=$rc seconds=$((SECONDS - start))" | tee -a "$results/$current/run.log"
  echo "$rc" >"$results/$current/rc"
  return "$rc"
}

write_summary() {
  [[ -d $results ]] || return 0
  {
    echo "# $NAME"
    echo
    echo '## Section 2: rotating the password before the migration'
    echo
    echo 'The app Pods before and after `aws secretsmanager rotate-secret`. External Secrets Operator updated the Kubernetes Secret and Reloader restarted the Pods. Start times are UTC.'
    echo
    echo '| | Pod | Started |'
    echo '|---|---|---|'
    jq -r '.[] | "| Before rotation | `" + .name + "` | " + .started + " |"' "$results/s2/pods-before.json" 2>/dev/null || true
    jq -r '.[] | "| After rotation | `" + .name + "` | " + .started + " |"' "$results/s2/pods-after.json" 2>/dev/null || true
    echo
    echo 'The first request that a Pod started after the rotation answered:'
    echo
    echo '```json'
    jq -c '.first_new_pod_response' "$results/s2/analysis.json" 2>/dev/null || echo null
    echo '```'
    echo
    echo '## Sections 3 to 5: failed requests during each step'
    echo
    echo 'A client in the cluster sent `GET /` to the app every 200 ms; each request runs one query on the DB. Each row counts the requests from the start of the step until 20 seconds after it finished (the `from` and `to` in `results/<step>/load.json`).'
    echo
    echo '| Step | Requests | Failures |'
    echo '|---|--:|--:|'
    local s label
    for s in s3 s4 s5; do
      case $s in
        s3) label='3: enable IAM authentication and create the IAM DB user' ;;
        s4) label='4: Pod Identity role and association, Pods recreated' ;;
        s5) label='5: switch the app to IAM authentication tokens' ;;
      esac
      jq -r --arg l "$label" '"| " + $l + " | " + (.requests | tostring) + " | " + (.failures | tostring) + " |"' "$results/$s/load.json" 2>/dev/null || true
    done
    echo
    echo '## Section 5: the app code before and after'
    echo
    echo '```diff'
    cat "$results/s5/app.diff" 2>/dev/null || true
    echo '```'
    echo
    echo '## Section 6: connecting through RDS Proxy'
    echo
    echo 'After the Pod role was allowed `rds-db:connect` on the proxy and `DB_HOST` was set to the proxy endpoint, the first request that a new Pod answered:'
    echo
    echo '```json'
    jq -c '.' "$results/s6/proxy.json" 2>/dev/null || echo null
    echo '```'
    echo
    echo '## A new connection more than 16 minutes after the app started'
    echo
    echo 'The app from section 5 (connection max lifetime 1 minute), checked once the proxy was ready. `token_built` is when the app logged that it built a token for a new connection; `response` is the first request answered on a connection opened after that.'
    echo
    echo '```json'
    jq -c '.' "$results/s6/late.json" 2>/dev/null || echo null
    echo '```'
    echo
    echo '## Numbers not used'
    echo
    echo '- Pod Identity token check (`results/token/tokencheck.log`): '"$(grep -c 'token check: ok' "$results/token/tokencheck.log" 2>/dev/null || echo 0)"' ok line'
    echo '- Section 2 requests and failures: `'"$(jq -c '{requests, failures}' "$results/s2/load.json" 2>/dev/null || echo null)"'`'
    echo '- Section 2 requests answered by the old Pods after AWSCURRENT moved: `'"$(jq -c '.old_pods_after_rotation' "$results/s2/analysis.json" 2>/dev/null || echo null)"'`'
    echo '- Section 6 requests and failures while switching to the proxy: `'"$(jq -c '{requests, failures}' "$results/s6/load.json" 2>/dev/null || echo null)"'`'
    echo -n '- Seconds per step:'
    for s in up token s2 s3 s4 s5 s6; do
      [[ -f $results/$s/seconds ]] && echo -n " $s $(cat "$results/$s/seconds"),"
    done
    echo
  } | mask >"$results/summary.md"
}

main() {
  install_tools
  mkdir -p "$state" "$results"
  if [[ $# -eq 0 ]]; then
    set -- token s2 s3 s4 s5 s6
  fi
  local s rc=0
  for s in "$@"; do
    case $s in
      up | token | s2 | s3 | s4 | s5 | s6 | reset | down)
        set +e
        stage "$s"
        rc=$?
        set -e
        ((rc == 0)) || break
        ;;
      summary) ;;
      *)
        echo "unknown step: $s" >&2
        exit 2
        ;;
    esac
  done
  write_summary
  exit "$rc"
}

main "$@"
