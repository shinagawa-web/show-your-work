#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
results=$here/results
TERRAFORM=1.17.1
CONFIGS=${*:-baseline a-ip-allowlist b-alb-cognito c-verified-access}
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
export TF_IN_AUTOMATION=1 TF_INPUT=0
export TF_PLUGIN_CACHE_DIR=$here/.bin/plugin-cache

read -r account caller_arn < <(aws sts get-caller-identity --query '[Account,Arn]' --output text)
role=$(cut -d/ -f2 <<<"$caller_arn")
session=$(cut -d/ -f3 <<<"$caller_arn")

mask() {
  sed -e "s/$account/111122223333/g" -e "s#assumed-role/$role/$session#assumed-role/verify/verify#g"
}

tf() {
  TF_DATA_DIR=$work/$cfg "$here/.bin/terraform" -chdir="$here/terraform/$cfg" "$@" -no-color
}

applied=
failures=0

leftovers() {
  local out
  {
    echo '$ aws ec2 describe-verified-access-endpoints'
    aws ec2 describe-verified-access-endpoints --query 'VerifiedAccessEndpoints[].VerifiedAccessEndpointId' --output json
    echo '$ aws ec2 describe-verified-access-groups'
    aws ec2 describe-verified-access-groups --query 'VerifiedAccessGroups[].VerifiedAccessGroupId' --output json
    echo '$ aws ec2 describe-verified-access-instances'
    aws ec2 describe-verified-access-instances --query 'VerifiedAccessInstances[].VerifiedAccessInstanceId' --output json
    echo '$ aws ec2 describe-verified-access-trust-providers'
    aws ec2 describe-verified-access-trust-providers --query 'VerifiedAccessTrustProviders[].VerifiedAccessTrustProviderId' --output json
    echo '$ aws elbv2 describe-load-balancers'
    aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output json
    echo '$ aws cognito-idp list-user-pools --max-results 60'
    aws cognito-idp list-user-pools --max-results 60 --query 'UserPools[].Id' --output json
    echo '$ aws acm list-certificates'
    aws acm list-certificates --query 'CertificateSummaryList[].CertificateArn' --output json
    echo '$ aws ec2 describe-vpcs --filters Name=tag:Name,Values=employee-apps'
    aws ec2 describe-vpcs --filters Name=tag:Name,Values=employee-apps --query 'Vpcs[].VpcId' --output json
  } 2>&1 | mask
}

destroy() {
  local start=$SECONDS
  if tf destroy -auto-approve 2>&1 | mask >"$results/$cfg/destroy.log"; then
    printf '%s\tdestroy\t%s\n' "$cfg" "$((SECONDS - start))" >>"$results/timings.tsv"
    applied=
  else
    failures=$((failures + 1))
    cat "$results/$cfg/destroy.log"
    return 1
  fi
}

finish() {
  local rc=$?
  if [[ -n $applied ]]; then destroy || rc=1; fi
  leftovers | tee "$results/leftovers.log"
  if grep -qE '^\s*"|An error occurred|Unable to' "$results/leftovers.log"; then
    echo 'Resources are left in the account, or a check failed, see results/leftovers.log'
    rc=1
  fi
  if [[ -f $results/prices/prices.json ]]; then
    python3 -I "$here/scripts/summary.py" "$results" "$here" || rc=1
  fi
  rm -rf "$work"
  exit "$rc"
}

check_alb() {
  local arn azs enis start=$SECONDS
  arn=$(aws elbv2 describe-load-balancers --names employee-apps --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  azs=$(aws elbv2 describe-load-balancers --names employee-apps --query 'length(LoadBalancers[0].AvailabilityZones)' --output text)
  while :; do
    enis=$(aws ec2 describe-network-interfaces --filters "Name=description,Values=ELB ${arn#*:loadbalancer/}" --query 'length(NetworkInterfaces)' --output text)
    if ((enis >= azs || SECONDS - start >= 300)); then break; fi
    sleep 10
  done
  {
    aws elbv2 describe-load-balancers --names employee-apps --query 'LoadBalancers[0].{Scheme:Scheme,State:State.Code,AvailabilityZones:length(AvailabilityZones)}' --output json
    aws ec2 describe-network-interfaces --filters "Name=description,Values=ELB ${arn#*:loadbalancer/}" \
      --query '{NetworkInterfaces:length(NetworkInterfaces),WithPublicIPv4:length(NetworkInterfaces[?Association.PublicIp])}' --output json
    echo "{\"SecondsWaitedForNetworkInterfaces\": $((SECONDS - start))}"
  } | mask >"$results/$cfg/alb.json"
}

check_endpoints() {
  aws ec2 describe-verified-access-endpoints \
    --query 'VerifiedAccessEndpoints[].{ApplicationDomain:ApplicationDomain,EndpointType:EndpointType,Status:Status.Code,LoadBalancerArn:LoadBalancerOptions.LoadBalancerArn}' \
    --output json | mask >"$results/$cfg/endpoints.json"
}

main() {
  if (($# == 0)); then rm -rf "$results"; fi
  mkdir -p "$results" "$TF_PLUGIN_CACHE_DIR"
  for cfg in $CONFIGS; do rm -rf "${results:?}/$cfg"; done
  if [[ -f $results/timings.tsv ]]; then
    for cfg in $CONFIGS; do grep -v "^$cfg	" "$results/timings.tsv" >"$results/timings.tmp" || true; mv "$results/timings.tmp" "$results/timings.tsv"; done
  fi
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

  leftovers >"$results/leftovers-before.log"
  if grep -qE '^\s*"|An error occurred|Unable to' "$results/leftovers-before.log"; then
    cat "$results/leftovers-before.log"
    echo 'The account already has resources of the kinds this run creates. Remove them first.'
    exit 1
  fi

  python3 -I "$here/scripts/prices.py" "$results/prices"

  export TF_VAR_cognito_domain_prefix=employee-apps-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')
  for cfg in $CONFIGS; do
    mkdir -p "$results/$cfg"
    tf init -lockfile=readonly 2>&1 | mask >"$results/$cfg/init.log"
  done
  for cfg in $CONFIGS; do
    local start=$SECONDS
    applied=yes
    if ! tf apply -auto-approve 2>&1 | mask >"$results/$cfg/apply.log"; then
      cat "$results/$cfg/apply.log"
      exit 1
    fi
    printf '%s\tapply\t%s\n' "$cfg" "$((SECONDS - start))" >>"$results/timings.tsv"
    tf show -json | python3 -I "$here/scripts/resources.py" | mask >"$results/$cfg/resources.json"
    check_alb
    if [[ $cfg == c-verified-access ]]; then check_endpoints; fi
    destroy
  done
}

(main "$@") 2>&1 | mask
