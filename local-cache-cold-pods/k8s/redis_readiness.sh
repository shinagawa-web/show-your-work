response=$(
  redis-cli \
    -h localhost \
    -p 6379 \
    ping
)
if [ "$response" != "PONG" ] ; then
  echo "ping=$response"
  exit 1
fi

response=$(
  redis-cli \
    -h localhost \
    -p 6379 \
    role
)
role=$( echo "$response" | sed "1!d" )
if [ "$role" = "master" ]; then
  echo "role=$role"
  exit 0
else
  echo "role=$role"
  exit 1
fi
