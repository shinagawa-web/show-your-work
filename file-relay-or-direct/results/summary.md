# file-relay-or-direct

## Requests without the app's permission

HTTP status for one object in `private/`, requested without the permission header `X-Allow: 1`: through the app, and with an S3 presigned URL and a CloudFront signed URL that the app had issued earlier to a request with the header.

| Request | Status |
|---|---|
| Relay through the app | 403 |
| S3 presigned URL issued by the app | 200 |
| CloudFront signed URL issued by the app | 200 |

## After the object is deleted from S3

HTTP status for one object in `private/`, requested through the app with the permission header and with the same two URLs issued by the app: after CloudFront cached the object, after the object was deleted from S3, and after a CloudFront invalidation of the path completed.

Each cell is the HTTP status and, when the response is an error, the error code in the body. The 403s are AccessDenied, not NoSuchKey: the app role and the bucket policy for CloudFront allow `s3:GetObject` but not `s3:ListBucket`, so S3 answers a request for a missing object with AccessDenied (with `s3:ListBucket` it is 404 NoSuchKey). They show that the object is gone, not that the URL stopped being valid. After the invalidation, CloudFront returns the AccessDenied that S3 sent to its request signed with origin access control. The relay returns 502, with the AccessDenied that S3 returned to its `GetObject` in the body.

| Request | Before deletion | After deletion | After invalidation |
|---|---|---|---|
| Relay through the app | 200 | 502 AccessDenied | 502 AccessDenied |
| S3 presigned URL | 200 | 403 AccessDenied | 403 AccessDenied |
| CloudFront signed URL | 200 | 200 | 403 AccessDenied |

## URL signed with credentials that expire in 15 minutes

An S3 presigned GET URL with `X-Amz-Expires=3600`, signed with the credentials of a role session assumed with `--duration-seconds 900`. HTTP status and, for the error, the error code in the body, right after signing and 30 seconds after the session credentials expired.

| When | Status and error code |
|---|---|
| Right after signing | 200 |
| After the credentials expired | 400 ExpiredToken |

Response body after the credentials expired:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Error><Code>ExpiredToken</Code><Message>The provided token has expired.</Message><Token-0>(session token removed)</Token-0><RequestId>CXAT8V7CJ5JP2PGR</RequestId><HostId>pNM3MxfGt4hv60+rTD1LxuGg4kr3t5shkSgJGKyobJDWU3AGiuoPh74jwiUkFyPqgb3tQWNFh92qRlzRIg12l/58rwlEx0Sd</HostId></Error>
```
