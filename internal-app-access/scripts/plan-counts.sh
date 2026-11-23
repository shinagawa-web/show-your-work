#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
results=$here/results/plans
terraform=$here/.bin/terraform
export AWS_REGION=us-east-1 TF_IN_AUTOMATION=1 TF_INPUT=0 TF_PLUGIN_CACHE_DIR=$here/.bin/plugin-cache
account=$(aws sts get-caller-identity --query Account --output text)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
(cd "$here" && tar --exclude='terraform.tfstate*' --exclude='.terraform.tfstate*' --exclude=.terraform --exclude=.terraform-build -cf - terraform) | tar -C "$work" -xf -
if find "$work" -name '*.tfstate*' | grep -q .; then echo 'a state file was copied' >&2; exit 1; fi
export TF_VAR_cognito_domain_prefix=employee-apps-plan
unset TF_VAR_app_domain_suffix TF_VAR_idc_group_id TF_VAR_saml_metadata_file TF_VAR_office_cidrs TF_VAR_attach_device_trust
rm -rf "$results"

plan() {
  local name=$1 dir=$2
  shift 2
  mkdir -p "$results/$name"
  export TF_DATA_DIR=$work/data-$dir
  "$terraform" -chdir="$work/terraform/$dir" init -lockfile=readonly -no-color >/dev/null
  "$terraform" -chdir="$work/terraform/$dir" plan -no-color -out="$work/$name.plan" "$@" 2>&1 | sed "s/$account/111122223333/g" >"$results/$name/plan.log"
  "$terraform" -chdir="$work/terraform/$dir" show -json "$work/$name.plan" | python3 -I "$here/scripts/plan-counts.py" >"$results/$name/resources.json"
  echo "$name: $(grep '^Plan:' "$results/$name/plan.log")"
}

plan baseline baseline
plan a-ip-allowlist a-ip-allowlist
plan b-alb-cognito b-alb-cognito
plan c-verified-access c-verified-access
plan c-without-device-trust c-verified-access -var attach_device_trust=false
