import json
import sys
from pathlib import Path

results = Path(sys.argv[1])
cases = sys.argv[2:]

POLICY_TYPES = {"aws_iam_policy", "aws_iam_role_policy"}

LABELS = {
    "baseline": "Baseline: `s3:GetObject`",
    "resource-star": "`s3:GetObject`",
    "action-star": "`Action *`",
    "s3-star": "`s3:*`",
    "not-action": "`NotAction` `iam:*`, `organizations:*`, `account:*`",
    "passrole-star": "`iam:PassRole` (aws_iam_policy)",
    "passrole-star-inline": "`iam:PassRole` (aws_iam_role_policy)",
    "passrole-app-path": "`iam:PassRole`",
    "create-policy-version": "`iam:CreatePolicyVersion`",
    "create-policy-version-star": "`iam:CreatePolicyVersion`",
    "action-typo": "`s3:GetObjcet`",
    "unknown-at-plan": "`iam:PassRole` and `s3:GetObject` on a bucket created in the same plan",
}

UNKNOWN = "unknown-at-plan"


def read(case, name):
    return (results / case / name).read_text()


def rc(case, name):
    return read(case, f"{name}.rc").strip()


def is_policy(resource):
    return resource.rsplit(".", 1)[0] in POLICY_TYPES


def checkov_rules(case):
    data = json.loads(read(case, "checkov.json"))
    return {c["check_id"] for c in data["results"]["failed_checks"] if is_policy(c["resource"])}


def trivy_rules(case):
    data = json.loads(read(case, "trivy.json"))
    found = set()
    for result in data.get("Results", []):
        for m in result.get("Misconfigurations", []):
            if m["Status"] == "FAIL" and is_policy(m["CauseMetadata"]["Resource"]):
                found.add(m["ID"])
    return found


def tfpv_output(case, name):
    text = read(case, f"{name}.out")
    marker = "Printing findings to the console..."
    body = text.split(marker, 1)[1] if marker in text else ""
    lines = [line for line in body.splitlines() if not line.startswith("api-call ")]
    report = json.loads("\n".join(lines)) if lines else {"BlockingFindings": [], "NonBlockingFindings": []}
    calls = [line for line in text.splitlines() if line.startswith("api-call ")]
    return report, calls, text


def findings(case, name):
    report, _, _ = tfpv_output(case, name)
    return {
        (f["policyName"], f["code"], f["findingType"])
        for f in report["BlockingFindings"] + report["NonBlockingFindings"]
        if is_policy(f["policyName"])
    }


def resources(case):
    plan = json.loads(read(case, "plan.json"))
    for r in plan["resource_changes"]:
        if r["type"] in POLICY_TYPES:
            policy = r["change"]["after"].get("policy")
            if policy is None:
                return "unknown at plan time"
            statements = json.loads(policy)["Statement"]
            return ", ".join(f"`{s['Resource']}`" for s in statements)


def cnna_calls(case):
    _, calls, _ = tfpv_output(case, "check-no-new-access")
    return [c for c in calls if c.startswith("api-call CheckNoNewAccess")]


def not_found_lines(case, name):
    _, _, text = tfpv_output(case, name)
    return [l.split(" - INFO - ", 1)[1] for l in text.splitlines() if "INFO - No policy found at: aws_iam_policy" in l]


def compress(ids):
    prefix = "CKV_AWS_"
    numbers = sorted(int(i[len(prefix):]) for i in ids if i.startswith(prefix))
    parts = []
    start = prev = None
    for n in numbers + [None]:
        if start is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            parts.append(str(start) if start == prev else f"{start}–{prev}" if prev - start > 1 else f"{start}, {prev}")
        start = prev = n
    parts += sorted(i for i in ids if not i.startswith(prefix))
    return ", ".join(parts)


def stopped(case):
    validate_skipped = bool(not_found_lines(case, "validate"))
    cnna_skipped = not cnna_calls(case)
    return [
        "stopped" if checkov_rules(case) else "",
        "stopped" if trivy_rules(case) else "",
        "not checked" if validate_skipped else "stopped" if rc(case, "validate") != "0" else "",
        "not checked" if cnna_skipped else "stopped" if rc(case, "check-no-new-access") != "0" else "",
    ]


def opening():
    out = [
        "## 1. Which tool stopped each policy",
        "",
        "For Checkov and Trivy, `stopped` means the tool reported a failed check on the policy resource, which alone makes it exit non-zero and fail a CI job. "
        "For ValidatePolicy and CheckNoNewAccess, it means tf-policy-validator exited non-zero, which it does on an ERROR or SECURITY_WARNING finding. "
        "`not checked` means tf-policy-validator found no policy to send for the policy resource.",
        "",
        "| Policy | Resource | Checkov | Trivy (`--exit-code 1`) | ValidatePolicy | CheckNoNewAccess |",
        "|---|---|---|---|---|---|",
    ]
    for case in cases:
        out.append(f"| {LABELS[case]} | {resources(case)} | " + " | ".join(stopped(case)) + " |")
    out.append("")
    return out


def setup():
    versions = (results / "versions.txt").read_text().splitlines()

    def pick(prefix):
        return next(line for line in versions if line.startswith(prefix))

    bundle, digest = pick("trivy checks bundle ").split()[3:5]
    release = pick("tf-policy-validator (release) ").split()[-1]
    text = (results / "tfpv-release.out").read_text().splitlines()
    error = next(line for line in text if line.startswith("ValueError"))
    return [
        "## 2. Setup",
        "",
        f"- Terraform {pick('Terraform ').split()[1].lstrip('v')}, AWS provider {pick('registry.terraform.io/hashicorp/aws ').split()[1]}",
        f"- Checkov {pick('checkov ').split()[1]}",
        f"- Trivy {pick('Version: ').split()[1]}, checks bundle `{bundle}` (`{digest}`)",
        f"- tf-policy-validator at commit `{pick('tf-policy-validator ').split()[1]}`",
        "",
        f"tf-policy-validator {release} from PyPI, on the plan JSON of {LABELS['passrole-star']} on `*`: exit code {(results / 'tfpv-release.rc').read_text().strip()}, `{error}`.",
        "",
    ]


def rules_section(title, intro, rows):
    out = [
        f"## {title}",
        "",
        (intro + " " if intro else "") + "What each tool reported on the policy; empty when it reported nothing. Checkov rules are `CKV_AWS_` numbers.",
        "",
        "| Policy | Resource | Checkov rules | Trivy rule | ValidatePolicy finding |",
        "|---|---|---|---|---|",
    ]
    for case in rows:
        vp = ", ".join(f"`{code}`" for _, code, _ in sorted(findings(case, "validate")))
        trivy = ", ".join(sorted(trivy_rules(case)))
        out.append(f"| {LABELS[case]} | {resources(case)} | {compress(checkov_rules(case))} | {trivy} | {vp} |")
    out.append("")
    return out


def unknown_section():
    plan = json.loads(read(UNKNOWN, "plan.json"))
    change = next(r for r in plan["resource_changes"] if r["address"] == "aws_iam_policy.app")["change"]
    calls = cnna_calls(UNKNOWN)
    return [
        "## 6. Policy unknown at plan time",
        "",
        f"{LABELS[UNKNOWN]}: `iam:PassRole` on `*`, and `s3:GetObject` on `${{aws_s3_bucket.app.arn}}/*`. In its plan JSON, `after_unknown.policy` of `aws_iam_policy.app` is `{json.dumps(change['after_unknown'].get('policy'))}`.",
        "",
        "| Tool | What it did |",
        "|---|---|",
        f"| Checkov | reported {compress(checkov_rules(UNKNOWN))} |",
        f"| Trivy | reported {', '.join(sorted(trivy_rules(UNKNOWN)))} |",
        "| tf-policy-validator validate | " + "<br>".join(f"`{l}`" for l in not_found_lines(UNKNOWN, "validate")) + " |",
        "| tf-policy-validator check-no-new-access | " + (f"{len(calls)} calls" if calls else "no CheckNoNewAccess call") + " |",
        "",
    ]


def main():
    out = [
        "# iam-policy-scanners",
        "",
        "One run of `run-all.sh`. Each policy is one file under `terraform/cases/`, attached to the role in `terraform/common.tf`, "
        "written with `jsonencode()` in `aws_iam_policy` unless the table says `aws_iam_role_policy`. "
        "Each policy has one problem; Resource is `*` only where Resource `*` is part of that problem.",
        "",
    ]
    out += opening()
    out += setup()
    out += rules_section(
        "3. Policies that allow actions together, and Resource `*` alone",
        "The first three allow many actions on one bucket; the last allows one action on Resource `*`.",
        ["action-star", "s3-star", "not-action", "resource-star"],
    )
    out += rules_section("4. iam:PassRole", "", ["passrole-star", "passrole-star-inline", "passrole-app-path"])
    out += rules_section("5. Privilege escalation and a misspelled action", "", ["create-policy-version", "create-policy-version-star", "action-typo"])
    out += unknown_section()
    (results / "summary.md").write_text("\n".join(out))


main()
