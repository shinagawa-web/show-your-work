import json
import sys
from pathlib import Path

results = Path(sys.argv[1])
cases = sys.argv[2:]

POLICY_TYPES = {"aws_iam_policy", "aws_iam_role_policy", "aws_iam_policy_document", "data.aws_iam_policy_document"}

LABELS = {
    "baseline": "Baseline: `s3:GetObject` on `app-bucket/*`",
    "action-star": "`Action *`",
    "s3-star": "`s3:*`",
    "not-action": "`NotAction`",
    "passrole-star": "`PassRole *` (aws_iam_policy)",
    "passrole-star-inline": "`PassRole *` (aws_iam_role_policy)",
    "passrole-app-path": "`PassRole role/app/*`",
    "passrole-app-path-condition": "`PassRole role/app/*` + `iam:PassedToService`",
    "create-policy-version": "`CreatePolicyVersion`",
    "action-typo": "`s3:GetObjcet` on `app-bucket/*`",
    "unknown-at-plan": "`PassRole *` + bucket created in the same plan",
    "action-star-policy-document": "`Action *` via `aws_iam_policy_document`",
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


def checkov_resources(case):
    data = json.loads(read(case, "checkov.json"))
    return {
        ("data." if c["resource"].startswith("aws_iam_policy_document.") else "") + c["resource"]
        for c in data["results"]["failed_checks"]
        if is_policy(c["resource"])
    }


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


def findings(case, name, policy_only=True):
    report, _, _ = tfpv_output(case, name)
    return {
        (f["policyName"], f["code"], f["findingType"])
        for f in report["BlockingFindings"] + report["NonBlockingFindings"]
        if policy_only == is_policy(f["policyName"])
    }


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
        "| Policy | Checkov | Trivy (`--exit-code 1`) | ValidatePolicy | CheckNoNewAccess |",
        "|---|---|---|---|---|",
    ]
    for case in cases:
        out.append(f"| {LABELS[case]} | " + " | ".join(stopped(case)) + " |")
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
    trust = {f for case in cases for f in findings(case, "validate", False)}
    same = all(findings(case, "validate", False) == findings("baseline", "validate", False) for case in cases)
    codes = ", ".join(f"`{code}` ({kind})" for _, code, kind in sorted(trust))
    return [
        "## 2. Setup",
        "",
        f"- Terraform {pick('Terraform ').split()[1].lstrip('v')}, AWS provider {pick('registry.terraform.io/hashicorp/aws ').split()[1]}",
        f"- Checkov {pick('checkov ').split()[1]}",
        f"- Trivy {pick('Version: ').split()[1]}, checks bundle `{bundle}` (`{digest}`)",
        f"- tf-policy-validator at commit `{pick('tf-policy-validator ').split()[1]}`",
        "",
        f"tf-policy-validator {release} from PyPI, on the plan JSON of `PassRole *`: exit code {(results / 'tfpv-release.rc').read_text().strip()}, `{error}`.",
        "",
        ("In every case" if same else "In some cases")
        + f", ValidatePolicy returned {codes} on the trust policy of the role, which does not stop.",
        "",
    ]


def rules_section(title, intro, rows):
    out = [
        f"## {title}",
        "",
        intro + " What each tool reported on the policy; empty when it reported nothing. Checkov rules are `CKV_AWS_` numbers.",
        "",
        "| Policy | Checkov rules | Trivy rule | ValidatePolicy finding |",
        "|---|---|---|---|",
    ]
    for case in rows:
        vp = ", ".join(f"`{code}`" for _, code, _ in sorted(findings(case, "validate")))
        trivy = ", ".join(sorted(trivy_rules(case)))
        out.append(f"| {LABELS[case]} | {compress(checkov_rules(case))} | {trivy} | {vp} |")
    out.append("")
    return out


def unknown_section():
    plan = json.loads(read(UNKNOWN, "plan.json"))
    change = next(r for r in plan["resource_changes"] if r["address"] == "aws_iam_policy.app")["change"]
    calls = cnna_calls(UNKNOWN)
    return [
        "## 6. Policy unknown at plan time",
        "",
        f"{LABELS[UNKNOWN]}. In its plan JSON, `after_unknown.policy` of `aws_iam_policy.app` is `{json.dumps(change['after_unknown'].get('policy'))}`.",
        "",
        "| Tool | What it did |",
        "|---|---|",
        f"| Checkov | reported {compress(checkov_rules(UNKNOWN))} |",
        f"| Trivy | reported {', '.join(sorted(trivy_rules(UNKNOWN)))} |",
        "| tf-policy-validator validate | " + "<br>".join(f"`{l}`" for l in not_found_lines(UNKNOWN, "validate")) + " |",
        "| tf-policy-validator check-no-new-access | " + (f"{len(calls)} calls" if calls else "no CheckNoNewAccess call") + " |",
        "",
    ]


def document_section():
    rows = ["action-star", "action-star-policy-document"]
    same = stopped(rows[0]) == stopped(rows[1])
    out = [
        "## jsonencode() and aws_iam_policy_document",
        "",
        "The same policy written with `jsonencode()` and with `data.aws_iam_policy_document`. Checkov rules are `CKV_AWS_` numbers.",
        "",
        "| Policy | Checkov rules |",
        "|---|---|",
    ]
    out += [f"| {LABELS[case]} | {compress(checkov_rules(case))} |" for case in rows]
    where = sorted(checkov_resources(rows[1]))
    if where == ["data.aws_iam_policy_document.app"]:
        note = "With `aws_iam_policy_document`, Checkov reports these rules on `data.aws_iam_policy_document.app`, not on `aws_iam_policy.app`."
    else:
        note = "With `aws_iam_policy_document`, Checkov reports these rules on " + ", ".join(f"`{r}`" for r in where) + "."
    out += ["", note, "", "The tools that stopped it are the same in both." if same else "The tools that stopped it differ; see section 1.", ""]
    return out


def main():
    out = [
        "# iam-policy-scanners",
        "",
        "One run of `run-all.sh`. Each policy is one file under `terraform/cases/`, attached to the role in `terraform/common.tf`, "
        "written with `jsonencode()` in `aws_iam_policy` and with Resource `*` unless its name says otherwise.",
        "",
    ]
    out += opening()
    out += setup()
    out += rules_section("3. Overly broad permissions", "`NotAction` lists `iam:*`, `organizations:*` and `account:*`.", ["action-star", "s3-star", "not-action"])
    out += rules_section(
        "4. iam:PassRole",
        "`role/app/*` is `arn:aws:iam::111122223333:role/app/*`.",
        ["passrole-star", "passrole-star-inline", "passrole-app-path", "passrole-app-path-condition"],
    )
    out += rules_section("5. Privilege escalation and a misspelled action", "`CreatePolicyVersion` is `iam:CreatePolicyVersion`.", ["create-policy-version", "action-typo"])
    out += unknown_section()
    out += document_section()
    (results / "summary.md").write_text("\n".join(out))


main()
