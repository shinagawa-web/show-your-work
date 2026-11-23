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

host_ip=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
host_ip_re=${host_ip//./\\.}
domain=${APP_DOMAIN_SUFFIX:-internal-app-access.example.com}
parent_domain=${domain#*.}
if [[ -n ${APP_DOMAIN_SUFFIX:-} ]]; then
  export TF_VAR_app_domain_suffix=$APP_DOMAIN_SUFFIX
else
  parent_domain=parent-domain-unset
fi

group_id=${VERIFIERS_GROUP_ID:-verifiers-group-id-unset}
if [[ -n ${VERIFIERS_GROUP_ID:-} ]]; then export TF_VAR_idc_group_id=$VERIFIERS_GROUP_ID; fi

mask() {
  sed -e "s/$account/111122223333/g" -e "s#assumed-role/$role/$session#assumed-role/verify/verify#g" -e "s/$host_ip_re/198.51.100.1/g" \
    -e "s/$group_id/<verifiers-group-id>/g" -e 's/identitystore_id=d-[0-9a-f]*/identitystore_id=d-0000000000/g' \
    -e "s/${parent_domain//./\\.}/example.com/g"
}

scan_results() {
  {
    grep -rlE -e "$account" -e "$role" -e "${host_ip//./\\.}" -e "$group_id" -e "${parent_domain//./\\.}" \
      -e 'PRIVATE KEY' -e '(ASIA|AKIA)[A-Z0-9]{16}' "$results" || true
    grep -roE '(^|[^A-Za-z0-9])(d-[0-9a-f]{10}|idpid=[A-Za-z0-9]+)' "$results" | grep -vE 'd-0000000000|idpid=C00000000' | cut -d: -f1 || true
  } | sort -u
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
    echo "$ aws ec2 describe-vpcs --filters 'Name=tag:Name,Values=employee-apps*'"
    aws ec2 describe-vpcs --filters 'Name=tag:Name,Values=employee-apps*' --query 'Vpcs[].VpcId' --output json
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
  local leaks
  leaks=$(scan_results)
  if [[ -n $leaks ]]; then
    echo "Values to mask remain in: $leaks"
    rc=1
  fi
  if [[ -f $results/prices/prices.json ]]; then
    python3 -I "$here/scripts/summary.py" "$results" "$here" || rc=1
  fi
  rm -rf "$work"
  exit "$rc"
}

check_alb() {
  local name arn azs enis start=$SECONDS
  name=$(alb_name)
  arn=$(aws elbv2 describe-load-balancers --names "$name" --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  azs=$(aws elbv2 describe-load-balancers --names "$name" --query 'length(LoadBalancers[0].AvailabilityZones)' --output text)
  while :; do
    enis=$(aws ec2 describe-network-interfaces --filters "Name=description,Values=ELB ${arn#*:loadbalancer/}" --query 'length(NetworkInterfaces)' --output text)
    if ((enis >= azs || SECONDS - start >= 300)); then break; fi
    sleep 10
  done
  {
    aws elbv2 describe-load-balancers --names "$name" --query 'LoadBalancers[0].{Scheme:Scheme,State:State.Code,AvailabilityZones:length(AvailabilityZones)}' --output json
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

probe() {
  python3 -I "$here/scripts/probe.py" --out "$results/$cfg/probes" --mask "$host_ip=198.51.100.1" --mask "$account=111122223333" \
    --mask "$group_id=<verifiers-group-id>" --mask "$parent_domain=example.com" "$@"
}

last_location() {
  python3 -I -c 'import json,sys; print(json.loads(open(sys.argv[1]).read().splitlines()[-1])["location"])' "$results/$cfg/probes/probes.jsonl"
}

alb_name() {
  tf output -raw alb_name
}

alb_dns() {
  aws elbv2 describe-load-balancers --names "$(alb_name)" --query 'LoadBalancers[0].DNSName' --output text
}

sg_rules() {
  local name sgs
  name=$(alb_name)
  {
    echo "# $1, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "\$ aws elbv2 describe-load-balancers --names $name --query 'LoadBalancers[0].SecurityGroups'"
    aws elbv2 describe-load-balancers --names "$name" --query 'LoadBalancers[0].SecurityGroups' --output json
    sgs=$(aws elbv2 describe-load-balancers --names "$name" --query 'LoadBalancers[0].SecurityGroups' --output text)
    for sg in $sgs; do
      echo "\$ aws ec2 describe-security-group-rules --filters Name=group-id,Values=$sg"
      aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$sg" \
        --query 'SecurityGroupRules[].{Id:SecurityGroupRuleId,Egress:IsEgress,Protocol:IpProtocol,From:FromPort,To:ToPort,Cidr:CidrIpv4,Group:ReferencedGroupInfo.GroupId}' --output json
    done
  } 2>&1 | mask
}

checks_a() {
  local dns start
  dns=$(alb_dns)
  probe --name allowed-cidr-is-this-host \
    --request "GET https://wiki.$domain/ to the ALB from this host, with this host's egress IP (/32) as the only allowed CIDR" \
    --prediction 'The app body is returned' \
    --url "https://wiki.$domain/" --connect-to "wiki.$domain:443:$dns:443" --wait-dns "$dns" --retry-seconds 180
  start=$SECONDS
  if ! TF_VAR_office_cidrs='["203.0.113.10/32"]' tf apply -auto-approve 2>&1 | mask >"$results/$cfg/apply-office-only.log"; then
    cat "$results/$cfg/apply-office-only.log"
    exit 1
  fi
  printf '%s\tapply-office-only\t%s\n' "$cfg" "$((SECONDS - start))" >>"$results/timings.tsv"
  sg_rules "after the apply with 203.0.113.10/32 only, before the curl below" >"$results/$cfg/sg-rules-before-blocked-probe.txt"
  probe --name allowed-cidr-is-203.0.113.10 \
    --request "GET https://wiki.$domain/ to the ALB from this host, with 203.0.113.10/32 as the only allowed CIDR" \
    --prediction 'The connection times out after 10 s and no app body is returned' \
    --url "https://wiki.$domain/" --connect-to "wiki.$domain:443:$dns:443" --until-no-body-seconds 180
  sg_rules "after the curl above" >"$results/$cfg/sg-rules-after-blocked-probe.txt"
}

checks_b() {
  local dns
  dns=$(alb_dns)
  probe --name unauthenticated-to-alb \
    --request "GET https://wiki.$domain/ to the ALB without a session cookie" \
    --prediction '302 to /oauth2/authorize on the Cognito domain, and no app body' \
    --url "https://wiki.$domain/" --connect-to "wiki.$domain:443:$dns:443" --wait-dns "$dns" --retry-seconds 0
  probe --name follow-authorize-once \
    --request 'GET the Location of the 302 above, once' \
    --prediction 'Cognito redirects to the SAML identity provider, the dummy Google SSO URL' \
    --url "$(last_location)"
}

checks_c() {
  local dns endpoint
  dns=$(alb_dns)
  endpoint=$(aws ec2 describe-verified-access-endpoints --query "VerifiedAccessEndpoints[?ApplicationDomain=='wiki.$domain'].EndpointDomain | [0]" --output text)
  probe --name unauthenticated-to-endpoint \
    --request "GET https://wiki.$domain/ to the Verified Access endpoint of wiki, without signing in" \
    --prediction 'A 3xx to the IAM Identity Center sign-in, and no app body' \
    --url "https://wiki.$domain/" --connect-to "wiki.$domain:443:$endpoint:443" --wait-dns "$endpoint"
  probe --name direct-to-internal-alb \
    --request "GET https://wiki.$domain/ to the internal ALB from this host" \
    --prediction 'The ALB name resolves only to private addresses, curl does not reach it, and no app body is returned' \
    --url "https://wiki.$domain/" --connect-to "wiki.$domain:443:$dns:443" --wait-dns "$dns"
}

refuse_existing_state() {
  local cfg state count refused=
  for cfg in $CONFIGS; do
    state=$here/terraform/$cfg/terraform.tfstate
    [[ -f $state ]] || continue
    count=$(python3 -I -c 'import json,sys; print(sum(len(r["instances"]) for r in json.load(open(sys.argv[1]))["resources"] if r["mode"] == "managed"))' "$state") || count=unknown
    if [[ $count != 0 && " ${ALLOW_EXISTING_STATE:-} " != *" $cfg "* ]]; then
      echo "$cfg has $count resources in its state. This run would change and destroy them."
      refused=yes
    fi
  done
  if [[ -n $refused ]]; then
    echo 'Nothing was run. To go on, name the configurations in ALLOW_EXISTING_STATE.'
    return 1
  fi
}

main() {
  refuse_existing_state || exit 1
  if (($# == 0)) && [[ -d $results ]]; then find "$results" -mindepth 1 -maxdepth 1 ! -name login -exec rm -rf {} +; fi
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
  if (($# == 0)) && grep -qE '^\s*"|An error occurred|Unable to' "$results/leftovers-before.log"; then
    cat "$results/leftovers-before.log"
    echo 'The account already has resources of the kinds this run creates. Remove them first.'
    exit 1
  fi

  "$here/scripts/plan-counts.sh"
  python3 -I "$here/scripts/prices.py" "$results/prices"

  export TF_VAR_office_cidrs="[\"$host_ip/32\"]"
  export TF_VAR_cognito_domain_prefix=${TF_VAR_cognito_domain_prefix:-employee-apps-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')}
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
    case $cfg in
      a-ip-allowlist) checks_a ;;
      b-alb-cognito) checks_b ;;
      c-verified-access) check_endpoints && checks_c ;;
    esac
    if [[ " ${KEEP:-} " == *" $cfg "* ]]; then
      applied=
      echo "$cfg is kept (KEEP). Destroy it with: terraform -chdir=terraform/$cfg destroy"
      continue
    fi
    destroy
  done
}

(main "$@") 2>&1 | mask
