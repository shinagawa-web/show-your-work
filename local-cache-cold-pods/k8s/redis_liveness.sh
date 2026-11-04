response=$(
  redis-cli \
    -h localhost \
    -p 6379 \
    ping
)
echo "response=$response"
case $response in
  PONG|LOADING*) ;;
  *) exit 1 ;;
esac
exit 0
