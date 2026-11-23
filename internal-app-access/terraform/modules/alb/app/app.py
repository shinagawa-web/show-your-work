import json


def handler(event, context):
    headers = event.get("headers") or {}
    body = {
        "message": f"reached app {headers.get('host', '')}",
        "method": event.get("httpMethod"),
        "path": event.get("path"),
        "headers": headers,
    }
    print(json.dumps(body))
    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "isBase64Encoded": False,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, indent=2) + "\n",
    }
