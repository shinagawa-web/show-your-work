import datetime
import json
import pathlib
import sys
import urllib.request

BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
REGION = "ap-northeast-1"

ITEMS = [
    ("alb_hour", "AWSELB", "APN1-LoadBalancerUsage", "LoadBalancing:Application", 0),
    ("alb_lcu_hour", "AWSELB", "APN1-LCUUsage", "LoadBalancing:Application", 0),
    ("public_ipv4_hour", "AmazonVPC", "APN1-PublicIPv4:InUseAddress", "", 0),
    ("cognito_saml_mau", "AmazonCognito", "APN1-CognitoEnterpriseMAU", "CognitoEnterpriseOperation", 0),
    ("cognito_saml_mau_free", "AmazonCognito", "Globalv1-CognitoEnterpriseMAU", "CognitoEnterpriseOperation", 0),
    ("verified_access_app_hour", "AmazonVPC", "APN1-VerifiedAccess-App-Hours", "VerifiedAccess-Apps", 0),
    ("verified_access_gb", "AmazonVPC", "APN1-VerifiedAccess-DataProcess-Bytes", "VerifiedAccess-data", 0),
]


def fetch(offer, out_dir):
    url = f"{BASE}/{offer}/current/{REGION}/index.json"
    with urllib.request.urlopen(url, timeout=60) as r:
        body = r.read()
    (out_dir / f"{offer}.json").write_bytes(body)
    return url, json.loads(body)


def dimensions(offer, usagetype, operation):
    found = []
    for sku, product in offer["products"].items():
        a = product["attributes"]
        if a.get("usagetype") != usagetype or a.get("operation", "") != operation:
            continue
        for term in offer["terms"].get("OnDemand", {}).get(sku, {}).values():
            for d in term["priceDimensions"].values():
                found.append({"sku": sku, **d})
    return found


def main():
    out_dir = pathlib.Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    offers = {}
    for _, code, _, _, _ in ITEMS:
        if code not in offers:
            url, data = fetch(code, out_dir)
            offers[code] = {"url": url, "data": data}
    prices = {}
    for key, code, usagetype, operation, begin in ITEMS:
        dims = dimensions(offers[code]["data"], usagetype, operation)
        tier = [d for d in dims if float(d["beginRange"]) == begin]
        if len(tier) != 1:
            print(f"{key}: expected one price dimension from {begin}, found {len(tier)}: {dims}", file=sys.stderr)
            sys.exit(1)
        d = tier[0]
        prices[key] = {
            "offer": code,
            "usagetype": usagetype,
            "operation": operation,
            "unit": d["unit"],
            "usd": d["pricePerUnit"]["USD"],
            "begin_range": d["beginRange"],
            "end_range": d["endRange"],
            "description": d["description"],
            "tiers": [
                {"begin_range": x["beginRange"], "end_range": x["endRange"], "usd": x["pricePerUnit"]["USD"]}
                for x in sorted(dims, key=lambda x: float(x["beginRange"]))
            ],
        }
    result = {
        "fetched_at": fetched_at,
        "offers": {
            code: {
                "url": v["url"],
                "publicationDate": v["data"]["publicationDate"],
                "version": v["data"]["version"],
            }
            for code, v in offers.items()
        },
        "prices": prices,
    }
    (out_dir / "prices.json").write_text(json.dumps(result, indent=2) + "\n")


main()
