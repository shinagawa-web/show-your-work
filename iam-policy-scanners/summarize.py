import json
import sys
from pathlib import Path

results = Path(sys.argv[1])
cases = sys.argv[2:]

POLICY_TYPES = {"aws_iam_policy", "aws_iam_role_policy", "aws_iam_policy_document", "data.aws_iam_policy_document"}

LABELS = {
    "baseline": "`s3:GetObject` on `arn:aws:s3:::app-bucket/*` (`aws_iam_policy`)",
    "action-star": "Action `*`, Resource `*`",
    "s3-star": "`s3:*`, Resource `*`",
    "not-action": "`NotAction` `iam:*`, `organizations:*`, `account:*`, Resource `*`",
    "passrole-star": "`iam:PassRole`, Resource `*`, in `aws_iam_policy`",
    "passrole-star-inline": "`iam:PassRole`, Resource `*`, in `aws_iam_role_policy`",
    "passrole-app-path": "`iam:PassRole`, Resource `role/app/*`",
    "passrole-app-path-condition": "`iam:PassRole`, Resource `role/app/*`, `iam:PassedToService` condition",
    "create-policy-version": "`iam:CreatePolicyVersion`, Resource `*`",
    "action-typo": "`s3:GetObjcet` (misspelled)",
    "unknown-at-plan": "`iam:PassRole` on `*` plus a statement on a bucket created in the same plan",
    "action-star-policy-document": "Action `*`, Resource `*`, through `data.aws_iam_policy_document`",
}


def read(case, name):
    return (results / case / name).read_text()


def rc(case, name):
    return read(case, f"{name}.rc").strip()


def is_policy(resource):
    return resource.rsplit(".", 1)[0] in POLICY_TYPES


def checkov_failed(case, policy_only=True):
    data = json.loads(read(case, "checkov.json"))
    found = {}
    for check in data["results"]["failed_checks"]:
        if policy_only != is_policy(check["resource"]):
            continue
        found.setdefault(check["resource"], []).append(check["check_id"])
    return found


def trivy_failed(case, policy_only=True):
    data = json.loads(read(case, "trivy.json"))
    found = {}
    for result in data.get("Results", []):
        for m in result.get("Misconfigurations", []):
            if m["Status"] != "FAIL":
                continue
            resource = m["CauseMetadata"]["Resource"]
            if policy_only != is_policy(resource):
                continue
            found.setdefault(resource, []).append(f"{m['ID']} {m['Severity']}")
    return found


def tfpv_output(case, name):
    text = read(case, f"{name}.out")
    marker = "Printing findings to the console..."
    body = text.split(marker, 1)[1] if marker in text else ""
    lines = [line for line in body.splitlines() if not line.startswith("api-call ")]
    report = json.loads("\n".join(lines)) if lines else {"BlockingFindings": [], "NonBlockingFindings": []}
    calls = [line for line in text.splitlines() if line.startswith("api-call ")]
    return report, calls, text


def tfpv_findings(case, name, policy_only=True):
    report, _, _ = tfpv_output(case, name)
    found = {}
    for finding in report["BlockingFindings"] + report["NonBlockingFindings"]:
        if policy_only != is_policy(finding["policyName"]):
            continue
        found.setdefault(finding["policyName"], []).append(f"{finding['code']} {finding['findingType']}")
    return found


def cell(found):
    if not found:
        return "none"
    parts = []
    for resource, items in found.items():
        parts.append(f"`{resource}`: " + ", ".join(f"`{i}`" for i in sorted(set(items))))
    return "<br>".join(parts)


def scanner_table(rows, policy_only=True):
    out = [
        "| Policy | Checkov failed checks | Checkov exit code | Trivy failures (ID, severity) | Trivy exit code | ValidatePolicy findings (code, type) | tf-policy-validator validate exit code |",
        "|---|---|--:|---|--:|---|--:|",
    ]
    for case in rows:
        out.append(
            f"| {LABELS[case]} | {cell(checkov_failed(case, policy_only))} | {rc(case, 'checkov')} "
            f"| {cell(trivy_failed(case, policy_only))} | {rc(case, 'trivy')} "
            f"| {cell(tfpv_findings(case, 'validate', policy_only))} | {rc(case, 'validate')} |"
        )
    return out


def scanner_section(title, intro, rows):
    out = [f"## {title}", "", intro, ""]
    out += scanner_table(rows)
    out.append("")
    return out


COMMON = (
    "One policy per row (`terraform/cases/<case>.tf`, `jsonencode()` in `aws_iam_policy` unless the row says otherwise), with the role in `terraform/common.tf`. "
    "Checkov and Trivy scan the Terraform directory; `tf-policy-validator validate` sends the policies in the plan JSON to Access Analyzer ValidatePolicy. "
    "Only findings on the policy resource are in this table. Checkov's JSON has `severity: null` for every check, so Checkov has no severity here."
)


def versions():
    lines = (results / "versions.txt").read_text().splitlines()
    out = ["## Section 2: versions", "", "From `results/versions.txt`, written by `run-all.sh` from each tool's version output.", "", "```"]
    out += lines
    out += ["```", ""]
    text = (results / "tfpv-release.out").read_text().strip().splitlines()
    picked = [line for line in text if line.startswith(("ValueError", "AttributeError"))]
    out += [
        "`tf-policy-validator validate` from PyPI, run once on the plan JSON of `iam:PassRole` on `*` (`results/passrole-star/plan.json`). "
        "Exit code, and the exception lines of its traceback (`results/tfpv-release.out`):",
        "",
        f"Exit code: {(results / 'tfpv-release.rc').read_text().strip()}",
        "",
        "```",
        *picked,
        "```",
        "",
    ]
    return out


def unknown_section():
    case = "unknown-at-plan"
    plan = json.loads(read(case, "plan.json"))
    change = next(r for r in plan["resource_changes"] if r["address"] == "aws_iam_policy.app")["change"]
    _, vcalls, vtext = tfpv_output(case, "validate")
    _, ccalls, ctext = tfpv_output(case, "check-no-new-access")
    logged = [l.split(" - INFO - ", 1)[1] for l in vtext.splitlines() if "INFO - No policy found at: aws_iam_policy" in l or "INFO - check policy at:" in l]
    out = [
        "## Section 6: a policy whose content is unknown at plan time",
        "",
        f"Policy: {LABELS[case]} (`terraform/cases/{case}.tf`).",
        "",
        "In the plan JSON (`results/unknown-at-plan/plan.json`), `aws_iam_policy.app`:",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| `change.after.policy` | `{json.dumps(change['after'].get('policy'))}` |",
        f"| `change.after_unknown.policy` | `{json.dumps(change['after_unknown'].get('policy'))}` |",
        "",
        "What each tool did with `aws_iam_policy.app`:",
        "",
        "| Tool | Output on `aws_iam_policy.app` | Exit code |",
        "|---|---|--:|",
        f"| Checkov | failed: {cell(checkov_failed(case))} | {rc(case, 'checkov')} |",
        f"| Trivy | failed: {cell(trivy_failed(case))} | {rc(case, 'trivy')} |",
        f"| tf-policy-validator validate | log: " + "<br>".join(f"`{l}`" for l in logged) + f"<br>Access Analyzer calls: " + ", ".join(f"`{c}`" for c in vcalls) + f" | {rc(case, 'validate')} |",
        f"| tf-policy-validator check-no-new-access | Access Analyzer calls: " + (", ".join(f"`{c}`" for c in ccalls) or "none") + f" | {rc(case, 'check-no-new-access')} |",
        "",
        "The ValidatePolicy call is for the trust policy of `aws_iam_role.app`.",
        "",
    ]
    return out


def custom_section(rows):
    out = [
        "## Section 7: CheckNoNewAccess against `reference-policy.json`",
        "",
        "`tf-policy-validator check-no-new-access --reference-policy-type identity` on each plan JSON. "
        "Calls are counted from the `api-call` lines `tfpv.py` prints for every Access Analyzer request, with the result Access Analyzer returned.",
        "",
        "| Policy | CheckNoNewAccess result | check-no-new-access exit code | CheckNoNewAccess calls |",
        "|---|---|--:|--:|",
    ]
    for case in rows:
        _, calls, _ = tfpv_output(case, "check-no-new-access")
        cnna = [c for c in calls if c.startswith("api-call CheckNoNewAccess")]
        results_ = ", ".join(c.split("result=", 1)[1] for c in cnna) or "not called"
        out.append(f"| {LABELS[case]} | {results_} | {rc(case, 'check-no-new-access')} | {len(cnna)} |")
    out.append("")
    return out


def same_in_every_case():
    base = tfpv_findings("baseline", "validate", False)
    differ = [case for case in cases if tfpv_findings(case, "validate", False) != base]
    if not differ:
        return "ValidatePolicy returned the same findings on the role in every case."
    return "ValidatePolicy returned different findings on the role in: " + ", ".join(differ) + "."


def main():
    out = ["# iam-policy-scanners", ""]
    out += versions()
    out += [
        "## Baseline",
        "",
        "One `aws_iam_policy` with `s3:GetObject` on `arn:aws:s3:::app-bucket/*` (`terraform/cases/baseline.tf`), next to the role in `terraform/common.tf`. "
        "This table lists the findings on every resource of the run, including the role and its trust policy. "
        "Exit codes: Checkov 1 when a check fails; Trivy 1 when a check fails (`--exit-code 1`); tf-policy-validator 2 when an ERROR or SECURITY_WARNING finding is found.",
        "",
        *scanner_table(["baseline"], policy_only=False),
        "",
        "`EMPTY_ARRAY_RESOURCE` is on the role's trust policy: tf-policy-validator sends it to ValidatePolicy with `\"Resource\": []` added, as its log in `results/baseline/validate.out` shows. "
        "It is a SUGGESTION, which does not change the exit code. "
        + same_in_every_case(),
        "",
    ]
    out += scanner_section("Section 3: overly broad permissions", COMMON, ["action-star", "s3-star", "not-action"])
    out += scanner_section(
        "Section 4: `iam:PassRole`",
        COMMON,
        ["passrole-star", "passrole-star-inline", "passrole-app-path", "passrole-app-path-condition"],
    )
    out += scanner_section(
        "Section 5: privilege escalation and a misspelled action", COMMON, ["create-policy-version", "action-typo"]
    )
    out += unknown_section()
    out += custom_section(
        [
            "action-star",
            "s3-star",
            "not-action",
            "passrole-star",
            "passrole-star-inline",
            "passrole-app-path",
            "passrole-app-path-condition",
            "create-policy-version",
            "action-typo",
            "unknown-at-plan",
        ]
    )
    out += scanner_section(
        "`jsonencode()` and `aws_iam_policy_document`",
        COMMON,
        ["action-star", "action-star-policy-document"],
    )
    (results / "summary.md").write_text("\n".join(out))


main()
