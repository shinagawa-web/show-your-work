#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=$here/results
TERRAFORM=1.17.1
WAIT_SECONDS=180
APP_ROLES="worker worker-no-boundary worker-tagged terraform-worker"

export AWS_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}
export AWS_REGION=${AWS_REGION:-us-east-1}

read -r account caller_arn < <(aws sts get-caller-identity --query '[Account,Arn]' --output text)
case $caller_arn in
  arn:aws:sts::*:assumed-role/*/*) ;;
  *) echo 'run-all.sh runs with role credentials (assumed-role)' >&2; exit 1 ;;
esac
reader_role_name=$(cut -d/ -f2 <<<"$caller_arn")
reader_session=$(cut -d/ -f3 <<<"$caller_arn")
if [[ -n ${GITHUB_ACTIONS:-} ]]; then
  echo "::add-mask::$account"
  echo "::add-mask::$reader_role_name"
fi

sed_escape() {
  printf '%s' "$1" | sed 's/[][\.*^$#/]/\\&/g'
}

role_re=$(sed_escape "$reader_role_name")
session_re=$(sed_escape "$reader_session")
name='[A-Za-z0-9+=,.@_-]'
other='[^A-Za-z0-9+=,.@_-]'

mask() {
  sed -e "s#assumed-role/$role_re/$session_re\$#assumed-role/admin/admin#" \
    -e "s#assumed-role/$role_re/$session_re\($other\)#assumed-role/admin/admin\1#g" \
    -e "s#\(:role/\($name*/\)*\)$role_re\$#\1admin#" \
    -e "s#\(:role/\($name*/\)*\)$role_re\($other\)#\1admin\3#g" \
    -e "s/$account/111122223333/g"
}

boundary_arn=arn:aws:iam::$account:policy/DevBoundary
developer_role_arn=arn:aws:iam::$account:role/syw-developer
app_role_arn=arn:aws:iam::$account:role/app/worker
failures=0
out=

as_developer() {
  env -u AWS_PROFILE AWS_ACCESS_KEY_ID="$dev_ak" AWS_SECRET_ACCESS_KEY="$dev_sk" AWS_SESSION_TOKEN="$dev_st" "$@"
}

as_app() {
  env -u AWS_PROFILE AWS_ACCESS_KEY_ID="$app_ak" AWS_SECRET_ACCESS_KEY="$app_sk" AWS_SESSION_TOKEN="$app_st" "$@"
}

assume() {
  aws sts assume-role --role-arn "$1" --role-session-name "$2" \
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text
}

wait_until() {
  local name=$1 cmd=$2 start=$SECONDS attempts=0
  while :; do
    attempts=$((attempts + 1))
    if out=$(eval "$cmd" 2>&1); then
      echo "$name: passed on attempt $attempts after $((SECONDS - start))s" | mask | tee -a "$results/waits.log"
      return 0
    fi
    if ((SECONDS - start >= WAIT_SECONDS)); then
      printf '%s: still failing after %s attempts in %ss\n%s\n' "$name" "$attempts" "$((SECONDS - start))" "$out" | mask | tee -a "$results/waits.log"
      return 1
    fi
    sleep 3
  done
}

check() {
  local section=$1 expect=$2 pattern=$3 runner=$4 cmd=$5 retry=${6:-} result verdict
  if [[ -n $retry ]]; then
    if wait_until "$cmd" "$runner $cmd"; then result=allowed; else result=denied; fi
  else
    if out=$(eval "$runner $cmd" 2>&1); then result=allowed; else result=denied; fi
  fi
  verdict=match
  if [[ $result != "$expect" ]]; then verdict=mismatch; fi
  if [[ $expect == denied ]] && ! grep -qF -- "$pattern" <<<"$out"; then verdict=mismatch; fi
  if [[ $verdict == mismatch ]]; then failures=$((failures + 1)); fi
  jq -cn --arg section "$section" --arg command "$cmd" --arg expect "$expect" --arg pattern "$pattern" \
    --arg result "$result" --arg verdict "$verdict" --arg output "$out" \
    '{$section, $command, $expect, $pattern, $result, $verdict, $output}' | mask >>"$results/checks.jsonl"
  printf '$ %s\n%s\n=> %s (expected %s): %s\n\n' "$cmd" "$out" "$result" "$expect" "$verdict" | mask | tee -a "$results/section$section.log"
}

delete_role() {
  local role=$1 p
  aws iam get-role --role-name "$role" >/dev/null 2>&1 || return 0
  for p in $(aws iam list-attached-role-policies --role-name "$role" --output json | jq -r '.AttachedPolicies[].PolicyArn'); do
    aws iam detach-role-policy --role-name "$role" --policy-arn "$p" || true
  done
  for p in $(aws iam list-role-policies --role-name "$role" --output json | jq -r '.PolicyNames[]'); do
    aws iam delete-role-policy --role-name "$role" --policy-name "$p" || true
  done
  aws iam delete-role --role-name "$role" || true
}

cleanup() {
  local role v
  for role in $APP_ROLES; do delete_role "$role"; done
  delete_role syw-developer
  if aws iam get-policy --policy-arn "$boundary_arn" >/dev/null 2>&1; then
    for v in $(aws iam list-policy-versions --policy-arn "$boundary_arn" --output json | jq -r '.Versions[] | select(.IsDefaultVersion | not) | .VersionId'); do
      aws iam delete-policy-version --policy-arn "$boundary_arn" --version-id "$v" || true
    done
    aws iam delete-policy --policy-arn "$boundary_arn" || true
  fi
}

leftovers() {
  aws iam list-roles --path-prefix /app/ --output json | jq -r '.Roles[].Arn'
  if aws iam get-role --role-name syw-developer >/dev/null 2>&1; then echo "$developer_role_arn"; fi
  if aws iam get-policy --policy-arn "$boundary_arn" >/dev/null 2>&1; then echo "$boundary_arn"; fi
}

write_summary() {
  [[ -f $results/checks.jsonl ]] || return 0
  local row
  row='select(.section == $s) | "| `" + .command + "` | " + .result + " | " + (if .result == "denied" then "`" + ([.output | splits("\n") | select(test("An error occurred"))] | join(" ")) + "`" elif (.output | test("^\\s*[0-9]+\\s*$")) then (.output | gsub("\\s"; "")) else "" end | gsub("\\|"; "\\|")) + " |"'
  {
    echo '# iam-permissions-boundary'
    echo
    echo '## Section 3: commands run as the developer'
    echo
    echo 'Run with the credentials of `role/syw-developer`, whose only policy is `policies/developer.json`, after `create-role` with the boundary had passed. `DevBoundary` is `policies/dev-boundary.json`. The message is the AWS CLI error line.'
    echo
    echo '| Command | Result | Error message |'
    echo '|---|---|---|'
    jq -r --arg s 3 "$row" "$results/checks.jsonl"
    echo
    echo '## Section 4: commands run with the role created in section 3'
    echo
    echo 'Run as the administrator after assuming `role/app/worker`, which has `AdministratorAccess` attached and `DevBoundary` as its permissions boundary, and after `list-buckets` had passed. For `list-buckets` the output is the number of buckets.'
    echo
    echo '| Command | Result | Output or error message |'
    echo '|---|---|---|'
    jq -r --arg s 4 "$row" "$results/checks.jsonl"
  } | mask >"$results/summary.md"
}

finish() {
  local rc=$? left
  cd "$here"
  write_summary || rc=1
  cleanup
  left=$(leftovers)
  if [[ -n $left ]]; then
    echo "$left" | mask | tee "$results/cleanup.log"
    rc=1
  else
    echo 'No /app/ roles, syw-developer or DevBoundary left' | tee "$results/cleanup.log"
  fi
  rm -rf "$work"
  exit "$rc"
}

main() {
  rm -rf "$results"
  mkdir -p "$results" "$here/.bin"
  work=$(mktemp -d)
  trap finish EXIT

  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  case $(uname -m) in
    x86_64 | amd64) arch=amd64 ;;
    *) arch=arm64 ;;
  esac
  curl -fsSLo "$work/terraform.zip" "https://releases.hashicorp.com/terraform/$TERRAFORM/terraform_${TERRAFORM}_${os}_${arch}.zip"
  unzip -oq "$work/terraform.zip" terraform -d "$here/.bin"

  reader_arn=$(aws iam list-roles --query "Roles[?RoleName=='$reader_role_name'].Arn | [0]" --output text)

  if [[ -n $(leftovers) ]]; then
    echo 'Roles or DevBoundary were left by an earlier run and are deleted now. Run again in a few minutes, after the deletion has been applied.'
    exit 1
  fi

  cp "$here/policies/dev-boundary.json" "$work/dev-boundary.json"
  sed "s/111122223333/$account/g" "$here/policies/developer.json" >"$work/developer.json"
  jq -n --arg p "$reader_arn" '{Version: "2012-10-17", Statement: [{Effect: "Allow", Principal: {AWS: $p}, Action: "sts:AssumeRole"}]}' >"$work/trust.json"
  mkdir "$work/terraform"
  cp "$here/terraform/main.tf" "$work/terraform/main.tf"
  cd "$work"

  {
    aws iam create-policy --policy-name DevBoundary --policy-document file://dev-boundary.json
    aws iam create-role --role-name syw-developer --assume-role-policy-document file://trust.json
    aws iam put-role-policy --role-name syw-developer --policy-name developer --policy-document file://developer.json
  } 2>&1 | mask | tee "$results/setup.log"

  wait_until 'assume role/syw-developer' "assume $developer_role_arn developer"
  read -r dev_ak dev_sk dev_st <<<"$out"

  check 3 allowed '' as_developer "aws iam create-role --role-name worker --path /app/ --assume-role-policy-document file://trust.json --permissions-boundary $boundary_arn" retry
  check 3 denied 'because no identity-based policy allows the iam:CreateRole action' as_developer \
    "aws iam create-role --role-name worker-no-boundary --path /app/ --assume-role-policy-document file://trust.json"
  check 3 allowed '' as_developer "aws iam attach-role-policy --role-name worker --policy-arn arn:aws:iam::aws:policy/AdministratorAccess"
  check 3 denied 'with an explicit deny in an identity-based policy' as_developer "aws iam delete-role-permissions-boundary --role-name worker"
  check 3 denied 'because no identity-based policy allows the iam:PutRolePermissionsBoundary action' as_developer \
    "aws iam put-role-permissions-boundary --role-name worker --permissions-boundary arn:aws:iam::aws:policy/AdministratorAccess"
  check 3 denied 'with an explicit deny in an identity-based policy' as_developer \
    "aws iam create-policy-version --policy-arn $boundary_arn --policy-document file://dev-boundary.json --set-as-default"

  local tf_ok=yes
  {
    export TF_IN_AUTOMATION=1 TF_VAR_boundary_arn=$boundary_arn TF_VAR_trusted_principal=$reader_arn
    as_developer "$here/.bin/terraform" -chdir=terraform init -input=false -no-color || tf_ok=no
    echo "terraform init: $tf_ok"
    as_developer "$here/.bin/terraform" -chdir=terraform apply -auto-approve -input=false -no-color || tf_ok=no
    echo "terraform apply: $tf_ok"
    as_developer "$here/.bin/terraform" -chdir=terraform destroy -auto-approve -input=false -no-color || tf_ok=no
    echo "terraform destroy: $tf_ok"
    echo "$tf_ok" >"$work/tf_ok"
  } 2>&1 | mask | tee "$results/terraform.log"
  if [[ $(cat "$work/tf_ok") != yes ]]; then failures=$((failures + 1)); fi

  local tag_cmd="aws iam create-role --role-name worker-tagged --path /app/ --assume-role-policy-document file://trust.json --permissions-boundary $boundary_arn --tags Key=team,Value=app"
  local tag_result=allowed
  if ! out=$(eval "as_developer $tag_cmd" 2>&1); then
    tag_result=denied
    failures=$((failures + 1))
  fi
  printf '$ %s\n%s\n=> %s (expected allowed)\n' "$tag_cmd" "$out" "$tag_result" | mask | tee "$results/tagged-create-role.log"

  wait_until 'assume role/app/worker' "assume $app_role_arn app"
  read -r app_ak app_sk app_st <<<"$out"

  check 4 allowed '' as_app "aws s3api list-buckets --query 'length(Buckets)'" retry
  check 4 denied 'because no permissions boundary allows the iam:ListUsers action' as_app "aws iam list-users"

  if ((failures > 0)); then
    echo "$failures checks did not match"
    exit 1
  fi
}

(main) 2>&1 | mask
