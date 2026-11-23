#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=$here/results
work=$here/.work
TERRAFORM=1.17.1
GO=1.27.3
INSTANCE_TYPE=m7i.large
phases=${*:-up measure}

export AWS_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}
export AWS_REGION=${AWS_REGION:-ap-northeast-1}
account=$(aws sts get-caller-identity --query Account --output text)
if [[ -n ${GITHUB_ACTIONS:-} ]]; then echo "::add-mask::$account"; fi

ip=
client_ip=$(curl -fsS https://checkip.amazonaws.com)

ip_re() {
  printf '%s' "$1" | sed 's/\./\\./g'
}

mask() {
  sed -E -e "s/$account/111122223333/g" \
    -e "s/$(ip_re "${ip:-0.0.0.0}")/203.0.113.10/g" \
    -e "s/$(ip_re "$client_ip")/198.51.100.10/g" \
    -e 's#<Token-0>[^<]*</Token-0>#<Token-0>(session token removed)</Token-0>#g' \
    -e 's/ASIA[A-Z0-9]{16}/ASIAIOSFODNN7EXAMPLE/g' \
    -e 's/AKIA[A-Z0-9]{16}/AKIAIOSFODNN7EXAMPLE/g'
}

tf() {
  "$here/.bin/terraform" -chdir="$here/terraform" "$@" -input=false -no-color
}

tfvars() {
  export TF_IN_AUTOMATION=1
  export TF_VAR_instance_type=$INSTANCE_TYPE
  export TF_VAR_app_binary=$work/relay-bench
  export TF_VAR_cf_public_key_pem=$(cat "$work/cf-key.pub")
  export TF_VAR_cf_private_key_pem=$(cat "$work/cf-key.pem")
  export TF_VAR_client_cidr=$client_ip/32
}

tools() {
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  case $(uname -m) in
    x86_64 | amd64) arch=amd64 ;;
    *) arch=arm64 ;;
  esac
  mkdir -p "$here/.bin" "$work"
  if ! "$here/.bin/terraform" version 2>/dev/null | grep -qF "v$TERRAFORM"; then
    curl -fsSLo "$work/terraform.zip" "https://releases.hashicorp.com/terraform/$TERRAFORM/terraform_${TERRAFORM}_${os}_${arch}.zip"
    unzip -oq "$work/terraform.zip" terraform -d "$here/.bin"
  fi
  if ! "$here/.bin/go/bin/go" version 2>/dev/null | grep -qF "go$GO "; then
    rm -rf "$here/.bin/go"
    curl -fsSL "https://go.dev/dl/go$GO.$os-$arch.tar.gz" | tar -xz -C "$here/.bin"
  fi
}

up() {
  tools
  (cd "$here/app" && GOOS=linux GOARCH=amd64 CGO_ENABLED=0 GOTOOLCHAIN=local "$here/.bin/go/bin/go" build -trimpath -o "$work/relay-bench" .)
  if [[ ! -f $work/cf-key.pem ]]; then
    openssl genrsa -traditional -out "$work/cf-key.pem" 2048 2>/dev/null
    openssl rsa -in "$work/cf-key.pem" -pubout -out "$work/cf-key.pub" 2>/dev/null
  fi
  tfvars
  local rc=0
  {
    tf init
    tf apply -auto-approve
  } >"$work/terraform-apply.log" 2>&1 || rc=$?
  outputs || true
  mask <"$work/terraform-apply.log" | tee "$results/terraform-apply.log" | tail -n 20
  return $rc
}

down() {
  tools
  tfvars
  tf destroy -auto-approve 2>&1 | mask | tee "$results/terraform-destroy.log" | tail -n 5
  leftovers | tee "$results/cleanup.log"
}

leftovers() {
  local left=0 n
  n=$(aws s3api list-buckets --query "length(Buckets[?starts_with(Name, 'file-relay-or-direct-')])" --output text)
  echo "S3 buckets file-relay-or-direct-*: $n"; ((n == 0)) || left=1
  n=$(aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running,stopping,stopped Name=instance-type,Values=$INSTANCE_TYPE --query 'length(Reservations[].Instances[])' --output text)
  echo "EC2 instances $INSTANCE_TYPE not terminated: $n"; ((n == 0)) || left=1
  n=$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=file-relay-or-direct --query 'length(Vpcs)' --output text)
  echo "VPCs file-relay-or-direct: $n"; ((n == 0)) || left=1
  n=$(aws cloudfront list-distributions --query 'length(DistributionList.Items || `[]`)' --output text)
  echo "CloudFront distributions: $n"; ((n == 0)) || left=1
  n=$(aws cloudfront list-public-keys --query 'length(PublicKeyList.Items || `[]`)' --output text)
  echo "CloudFront public keys: $n"; ((n == 0)) || left=1
  n=$(aws iam list-roles --query "length(Roles[?starts_with(RoleName, 'syw-file-relay-')])" --output text)
  echo "IAM roles syw-file-relay-*: $n"; ((n == 0)) || left=1
  n=$(aws iam list-instance-profiles --query "length(InstanceProfiles[?starts_with(InstanceProfileName, 'syw-file-relay-')])" --output text)
  echo "IAM instance profiles syw-file-relay-*: $n"; ((n == 0)) || left=1
  return $left
}

outputs() {
  local o
  o=$("$here/.bin/terraform" -chdir="$here/terraform" output -json)
  bucket=$(jq -r .bucket.value <<<"$o")
  ip=$(jq -r .app_ip.value <<<"$o")
  cf_domain=$(jq -r .cf_domain.value <<<"$o")
  cf_id=$(jq -r .cf_distribution_id.value <<<"$o")
  signer_role=$(jq -r .signer_role_arn.value <<<"$o")
  ami=$(jq -r .ami.value <<<"$o")
  az=$(jq -r .availability_zone.value <<<"$o")
  app=http://$ip:8080
}

failures=0
fail() {
  echo "INVALID: $*" | tee -a "$results/invalid.log"
  failures=$((failures + 1))
}

wait_ready() {
  local i
  for i in $(seq 1 120); do
    if curl -fsS -m 3 "$app/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 5
  done
  return 1
}

fetch() {
  local name=$1 url=$2 out
  shift 2
  out=$(curl -sS -D "$work/headers" -o "$work/body" -w '%{http_code}' "$@" "$url")
  local body=
  if [[ $out != 200 ]] || (($(wc -c <"$work/body") < 200)); then body=$(head -c 2000 "$work/body" | LC_ALL=C tr -cd '[:print:]\n'); fi
  jq -cn --arg name "$name" --arg status "$out" --arg x_cache "$(grep -i '^x-cache:' "$work/headers" | cut -d' ' -f2- | tr -d '\r')" \
    --arg body "$body" --argjson bytes "$(wc -c <"$work/body")" --arg at "$(date -u +%FT%TZ)" \
    '{$name, $at, status: ($status | tonumber), $x_cache, $bytes, $body}' | mask | tee -a "$results/$log"
}

status_of() {
  jq -r --arg n "$1" 'select(.name == $n) | .status' "$results/$log" | tail -n 1
}

expiry_sign() {
  local creds i
  log=expiry.jsonl
  rm -f "$results/$log" "$work/expiry.env" "$results/invalid.log"
  echo "expiry check object" >"$work/expiry.txt"
  aws s3 cp "$work/expiry.txt" "s3://$bucket/private/expiry/doc.txt" >/dev/null
  for i in $(seq 1 20); do
    if creds=$(aws sts assume-role --role-arn "$signer_role" --role-session-name signer --duration-seconds 900 --output json 2>"$work/assume.err"); then break; fi
    sleep 5
  done
  [[ -n ${creds:-} ]] || { mask <"$work/assume.err"; fail "assume-role $signer_role failed"; return; }
  expiry_at=$(jq -r .Credentials.Expiration <<<"$creds")
  expiry_url=$(env -u AWS_PROFILE AWS_ACCESS_KEY_ID="$(jq -r .Credentials.AccessKeyId <<<"$creds")" \
    AWS_SECRET_ACCESS_KEY="$(jq -r .Credentials.SecretAccessKey <<<"$creds")" \
    AWS_SESSION_TOKEN="$(jq -r .Credentials.SessionToken <<<"$creds")" \
    aws s3 presign "s3://$bucket/private/expiry/doc.txt" --expires-in 3600)
  printf 'expiry_at=%q\nexpiry_url=%q\n' "$expiry_at" "$expiry_url" >"$work/expiry.env"
  jq -cn --arg name signed --arg at "$(date -u +%FT%TZ)" --arg credentials_expire "$expiry_at" \
    --arg x_amz_expires "$(grep -o 'X-Amz-Expires=[0-9]*' <<<"$expiry_url")" \
    '{$name, $at, $credentials_expire, $x_amz_expires}' | tee -a "$results/$log"
  fetch right-after-signing "$expiry_url"
  [[ $(status_of right-after-signing) == 200 ]] || fail "S3 presigned URL from the 900-second session did not return 200 right after signing"
}

expiry_check() {
  local exp_epoch now
  log=expiry.jsonl
  if [[ -z ${expiry_url:-} && -f $work/expiry.env ]]; then source "$work/expiry.env"; fi
  [[ -n ${expiry_url:-} ]] || { fail "no URL from expiry-sign"; return; }
  exp_epoch=$(date -j -u -f '%Y-%m-%dT%H:%M:%S' "${expiry_at%%[+Z]*}" +%s 2>/dev/null || date -u -d "$expiry_at" +%s)
  now=$(date -u +%s)
  if ((now < exp_epoch + 30)); then
    echo "waiting $((exp_epoch + 30 - now))s until 30s after the session credentials expire"
    sleep $((exp_epoch + 30 - now))
  fi
  fetch after-credentials-expired "$expiry_url"
}

deletion_check() {
  local key=private/deleted/doc.txt s3_url cf_url inv
  rm -f "$results/deletion.jsonl"
  log=deletion.jsonl
  echo "deletion check object" >"$work/deleted.txt"
  aws s3 cp "$work/deleted.txt" "s3://$bucket/$key" >/dev/null
  s3_url=$(curl -fsS -H 'X-Allow: 1' "$app/sign/s3-get?key=$key")
  cf_url=$(curl -fsS -H 'X-Allow: 1' "$app/sign/cf?key=$key")
  fetch s3-before-delete "$s3_url"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    fetch cf-before-delete "$cf_url"
    jq -e 'select(.name == "cf-before-delete") | .x_cache | startswith("Hit")' "$results/$log" >/dev/null && break
    sleep 1
  done
  [[ $(status_of s3-before-delete) == 200 ]] || fail "S3 presigned URL did not return 200 before deletion"
  [[ $(status_of cf-before-delete) == 200 ]] || fail "CloudFront signed URL did not return 200 before deletion"
  [[ $(jq -r 'select(.name == "cf-before-delete") | .x_cache' "$results/$log" | tail -n 1) == Hit* ]] || fail "CloudFront did not serve the object from cache before deletion"
  aws s3api delete-object --bucket "$bucket" --key "$key" >/dev/null
  fetch s3-after-delete "$s3_url"
  fetch cf-after-delete "$cf_url"
  inv=$(aws cloudfront create-invalidation --distribution-id "$cf_id" --paths "/$key" --query Invalidation.Id --output text)
  aws cloudfront wait invalidation-completed --distribution-id "$cf_id" --id "$inv"
  fetch s3-after-invalidation "$s3_url"
  fetch cf-after-invalidation "$cf_url"
}

access_checks() {
  local put_url get_url cf_url
  log=access.jsonl
  rm -f "$results/$log"
  put_url=$(curl -fsS "$app/sign/s3-put?key=public/direct/2M")
  fetch s3-presigned-put "$put_url" -X PUT -T "$work/files/2M.bin"
  get_url=$(curl -fsS -H 'X-Allow: 1' "$app/sign/s3-get?key=private/access/2M")
  fetch s3-presigned-get "$get_url"
  cf_url=$(curl -fsS -H 'X-Allow: 1' "$app/sign/cf?key=private/access/2M")
  fetch cf-signed-private "$cf_url"
  fetch cf-unsigned-public "https://$cf_domain/public/direct/2M"
  fetch cf-unsigned-private "https://$cf_domain/private/access/2M"
  fetch relay-download-without-header "$app/download?key=private/access/2M"
  fetch sign-cf-without-header "$app/sign/cf?key=private/access/2M"
  local name want
  for name in s3-presigned-put:200 s3-presigned-get:200 cf-signed-private:200 cf-unsigned-public:200 cf-unsigned-private:403 relay-download-without-header:403 sign-cf-without-header:403; do
    want=${name#*:}
    [[ $(status_of "${name%%:*}") == "$want" ]] || fail "${name%%:*} did not return $want"
  done
}

prepare() {
  tools
  outputs
  wait_ready || { fail "the app on $ip did not become ready"; exit 1; }
  {
    echo "instance type: $INSTANCE_TYPE"
    echo "AMI: $ami"
    echo "availability zone: $az"
    echo "region: $AWS_REGION"
    echo "Go: $GO"
    grep -E 'aws-sdk-go-v2(/service/s3|/feature/s3/transfermanager|/feature/cloudfront/sign)? ' "$here/app/go.mod" | sed 's/^\t//'
  } | tee "$results/environment.txt"

  mkdir -p "$work/files"
  head -c 2097152 /dev/urandom >"$work/files/2M.bin"
  aws s3 cp "$work/files/2M.bin" "s3://$bucket/private/access/2M" >/dev/null
}

measure() {
  expiry-sign
  access
  deletion
  expiry-check
}

expiry-sign() { prepare; expiry_sign; write_summary; }
access() { prepare; access_checks; write_summary; }
deletion() { prepare; deletion_check; write_summary; }
expiry-check() { tools; outputs; expiry_check; write_summary; }
summary() { tools; write_summary; }

status_row() {
  local label=$1 prefix=$2
  printf '| %s | %s | %s | %s |\n' "$label" "$(status_of "$prefix-before-delete")" "$(status_of "$prefix-after-delete")" "$(status_of "$prefix-after-invalidation")"
}

write_summary() {
  {
    echo '# file-relay-or-direct'
    echo
    echo '## After the object is deleted from S3'
    echo
    echo 'HTTP status of the same two URLs, issued by the app for one object in `private/`: after CloudFront cached the object, after the object was deleted from S3, and after a CloudFront invalidation of the path completed.'
    echo
    log=deletion.jsonl
    echo '| URL | Before deletion | After deletion | After invalidation |'
    echo '|---|---:|---:|---:|'
    status_row 'S3 presigned URL' s3
    status_row 'CloudFront signed URL' cf
    echo
    log=expiry.jsonl
    echo '## URL signed with credentials that expire in 15 minutes'
    echo
    echo 'An S3 presigned GET URL with `X-Amz-Expires=3600`, signed with the credentials of a role session assumed with `--duration-seconds 900`. Status right after signing, and 30 seconds after the session credentials expired.'
    echo
    echo '| When | Status |'
    echo '|---|---:|'
    echo "| Right after signing | $(status_of right-after-signing) |"
    echo "| After the credentials expired | $(status_of after-credentials-expired) |"
    echo
    echo 'Response body after the credentials expired:'
    echo
    echo '```xml'
    jq -r 'select(.name == "after-credentials-expired") | .body' "$results/$log" 2>/dev/null || true
    echo '```'
  } | mask >"$results/summary.md"
}

main() {
  mkdir -p "$results" "$work"
  local p
  for p in $phases; do
    case $p in
      up | measure | expiry-sign | access | deletion | expiry-check | summary | down) ;;
      *) echo "unknown phase $p (up, measure, expiry-sign, access, deletion, expiry-check, summary, down)" >&2; exit 2 ;;
    esac
  done
  for p in $phases; do
    echo "== $p ($(date -u +%T))"
    "$p"
  done
  if ((failures > 0)); then
    echo "$failures checks were invalid"
    exit 1
  fi
}

main
