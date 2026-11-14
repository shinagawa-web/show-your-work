#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=$here/results
bin=$here/.bin
TERRAFORM=1.17.2
CHECKOV=3.4.3
TRIVY=0.76.2
TRIVY_CHECKS=mirror.gcr.io/aquasec/trivy-checks:2.3.0
TFPV_COMMIT=0872b5d4a9d1ecb8bc2207e64ff4dd2409a78352
TFPV_RELEASE=0.0.9
REGION=us-east-1
PYTHON=${PYTHON:-python3}
CASES="baseline action-star s3-star not-action passrole-star passrole-star-inline passrole-app-path passrole-app-path-condition create-policy-version action-typo unknown-at-plan action-star-policy-document"

account=$(aws sts get-caller-identity --query Account --output text)
if [[ -n ${GITHUB_ACTIONS:-} ]]; then
  echo "::add-mask::$account"
fi

mask() {
  sed -e "s/$account/111122223333/g" -e "s#$here#.#g"
}

install() {
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  case $(uname -m) in
    x86_64 | amd64) arch=amd64 ;;
    *) arch=arm64 ;;
  esac
  mkdir -p "$bin"
  if [[ ! -x $bin/terraform ]] || ! "$bin/terraform" version | grep -qF "v$TERRAFORM"; then
    curl -fsSLo "$work/terraform.zip" "https://releases.hashicorp.com/terraform/$TERRAFORM/terraform_${TERRAFORM}_${os}_${arch}.zip"
    unzip -oq "$work/terraform.zip" terraform -d "$bin"
  fi
  if [[ ! -x $bin/trivy ]] || ! "$bin/trivy" --version | grep -qF "$TRIVY"; then
    local tos=Linux tarch=64bit
    [[ $os == darwin ]] && tos=macOS
    [[ $arch == arm64 ]] && tarch=ARM64
    curl -fsSLo "$work/trivy.tgz" "https://github.com/aquasecurity/trivy/releases/download/v$TRIVY/trivy_${TRIVY}_${tos}-${tarch}.tar.gz"
    tar -xzf "$work/trivy.tgz" -C "$bin" trivy
  fi
  if [[ ! -x $bin/checkov/bin/checkov ]]; then
    "$PYTHON" -m venv "$bin/checkov"
    "$bin/checkov/bin/pip" install -q "checkov==$CHECKOV"
  fi
  if [[ ! -x $bin/tfpv/bin/tf-policy-validator ]]; then
    "$PYTHON" -m venv "$bin/tfpv"
    "$bin/tfpv/bin/pip" install -q "git+https://github.com/awslabs/terraform-iam-policy-validator@$TFPV_COMMIT"
  fi
  if [[ ! -x $bin/tfpv-release/bin/tf-policy-validator ]]; then
    "$PYTHON" -m venv "$bin/tfpv-release"
    "$bin/tfpv-release/bin/pip" install -q "tf-policy-validator==$TFPV_RELEASE"
  fi
}

tfpv_config() {
  "$1/bin/python" -c 'import iam_check, os; print(os.path.join(os.path.dirname(iam_check.__file__), "config", "default.yaml"))'
}

tf() {
  env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_ROLE_ARN -u AWS_WEB_IDENTITY_TOKEN_FILE \
    AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null TF_IN_AUTOMATION=1 TF_PLUGIN_CACHE_DIR="$work/plugin-cache" "$bin/terraform" "$@"
}

plan() {
  local c=$1 dir=$work/plan/$1
  mkdir -p "$dir"
  cp -R "$work/init/.terraform" "$work/init/.terraform.lock.hcl" "$dir/"
  cp "$here/terraform/common.tf" "$here/terraform/cases/$c.tf" "$dir/"
  (
    cd "$dir"
    tf plan -input=false -no-color -out=tfplan
    tf show -json tfplan >"$results/$c/plan.json"
  ) >"$results/$c/terraform.log" 2>&1
}

scan() {
  local c=$1 code=$work/code/$1 rc
  mkdir -p "$code"
  cp "$here/terraform/common.tf" "$here/terraform/cases/$c.tf" "$code/"
  rc=0
  "$bin/checkov/bin/checkov" -d "$code" --framework terraform -o json >"$results/$c/checkov.json" 2>"$results/$c/checkov.err" || rc=$?
  echo "$rc" >"$results/$c/checkov.rc"
  rc=0
  "$bin/trivy" config --checks-bundle-repository "$TRIVY_CHECKS" --cache-dir "$work/trivy-cache" \
    --exit-code 1 -f json "$code" >"$results/$c/trivy.json" 2>"$results/$c/trivy.err" || rc=$?
  echo "$rc" >"$results/$c/trivy.rc"
}

analyze() {
  local c=$1 rc
  rc=0
  "$bin/tfpv/bin/python" "$here/tfpv.py" validate --template-path "$results/$c/plan.json" --region "$REGION" \
    --config "$tfpv_cfg" --enable-logging >"$work/out" 2>&1 || rc=$?
  mask <"$work/out" >"$results/$c/validate.out"
  echo "$rc" >"$results/$c/validate.rc"
  rc=0
  "$bin/tfpv/bin/python" "$here/tfpv.py" check-no-new-access --template-path "$results/$c/plan.json" --region "$REGION" \
    --config "$tfpv_cfg" --reference-policy "$here/reference-policy.json" --reference-policy-type identity \
    --enable-logging >"$work/out" 2>&1 || rc=$?
  mask <"$work/out" >"$results/$c/check-no-new-access.out"
  echo "$rc" >"$results/$c/check-no-new-access.rc"
}

main() {
  rm -rf "$results"
  mkdir -p "$results"
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  local c start=$SECONDS

  install
  tfpv_cfg=$(tfpv_config "$bin/tfpv")
  mkdir -p "$work/init" "$work/plugin-cache"
  cp "$here/terraform/common.tf" "$work/init/"
  (cd "$work/init" && tf init -input=false -no-color) >"$results/terraform-init.log" 2>&1
  for c in $CASES; do
    mkdir -p "$results/$c"
    plan "$c"
  done
  echo "plans: $((SECONDS - start))s"

  for c in $CASES; do scan "$c"; done
  echo "checkov and trivy: $((SECONDS - start))s"

  for c in $CASES; do analyze "$c"; done
  echo "tf-policy-validator: $((SECONDS - start))s"

  local rc=0
  "$bin/tfpv-release/bin/tf-policy-validator" validate --template-path "$results/passrole-star/plan.json" --region "$REGION" \
    --config "$(tfpv_config "$bin/tfpv-release")" >"$work/out" 2>&1 || rc=$?
  mask <"$work/out" >"$results/tfpv-release.out"
  echo "$rc" >"$results/tfpv-release.rc"

  {
    "$bin/terraform" version
    (cd "$work/init" && "$bin/terraform" version -json | jq -r '.provider_selections | to_entries[] | "\(.key) \(.value)"')
    echo "checkov $("$bin/checkov/bin/checkov" --version)"
    "$bin/trivy" --version --cache-dir "$work/trivy-cache"
    echo "trivy checks bundle $TRIVY_CHECKS $(jq -r .Digest "$work/trivy-cache/policy/metadata.json")"
    echo "tf-policy-validator $TFPV_COMMIT"
    echo "tf-policy-validator (release) $("$bin/tfpv-release/bin/pip" show tf-policy-validator | awk '/^Version/ {print $2}')"
  } >"$results/versions.txt" 2>&1

  echo "total: $((SECONDS - start))s" | tee "$results/duration.txt"
  "$PYTHON" "$here/summarize.py" "$results" $CASES
}

main 2>&1 | mask
