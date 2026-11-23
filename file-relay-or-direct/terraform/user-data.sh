#!/bin/bash
set -euo pipefail
echo "binary ${binary_etag}"
mkdir -p /etc/relay-bench
cat >/etc/relay-bench/cf-key.pem <<'KEY'
${cf_private_key}
KEY
chmod 600 /etc/relay-bench/cf-key.pem
for i in $(seq 1 30); do
  aws s3 cp s3://${bucket}/bin/relay-bench /usr/local/bin/relay-bench --region ${region} && break
  sleep 5
done
chmod +x /usr/local/bin/relay-bench
cat >/etc/systemd/system/relay-bench.service <<'UNIT'
[Service]
Environment=BUCKET=${bucket}
Environment=AWS_REGION=${region}
Environment=CF_DOMAIN=${cf_domain}
Environment=CF_KEY_ID=${cf_key_id}
Environment=CF_KEY_FILE=/etc/relay-bench/cf-key.pem
ExecStart=/usr/local/bin/relay-bench monitor
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now relay-bench
