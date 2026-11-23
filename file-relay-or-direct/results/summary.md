# file-relay-or-direct

## After the object is deleted from S3

HTTP status for one object in `private/`, requested through the app with the permission header and with the same two URLs issued by the app: after CloudFront cached the object, after the object was deleted from S3, and after a CloudFront invalidation of the path completed.

| Request | Before deletion | After deletion | After invalidation |
|---|---:|---:|---:|
| Relay through the app | 200 | 502 | 502 |
| S3 presigned URL | 200 | 403 | 403 |
| CloudFront signed URL | 200 | 200 | 403 |

## URL signed with credentials that expire in 15 minutes

An S3 presigned GET URL with `X-Amz-Expires=3600`, signed with the credentials of a role session assumed with `--duration-seconds 900`. Status right after signing, and 30 seconds after the session credentials expired.

| When | Status |
|---|---:|
| Right after signing | 200 |
| After the credentials expired | 400 |

Response body after the credentials expired:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Error><Code>ExpiredToken</Code><Message>The provided token has expired.</Message><Token-0>(session token removed)</Token-0><RequestId>CXAT8V7CJ5JP2PGR</RequestId><HostId>pNM3MxfGt4hv60+rTD1LxuGg4kr3t5shkSgJGKyobJDWU3AGiuoPh74jwiUkFyPqgb3tQWNFh92qRlzRIg12l/58rwlEx0Sd</HostId></Error>
```
