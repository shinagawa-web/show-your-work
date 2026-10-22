q="check=$(date +%s%N)"
url="http://localhost/items/1?$q"
sid=$(curl -s -D - -o /dev/null "http://localhost/login?user=checker" | sed -n 's/^[Ss]et-[Cc]ookie: session=\([0-9a-f]*\).*/\1/p')
mask() { if [ -n "$sid" ]; then sed "s/$sid/<checker>/g"; else cat; fi; }
[ -n "$sid" ] || echo "login failed: no session cookie from /login?user=checker"
echo "\$ curl -s -D - -H 'Cookie: session=<checker>' '$url'"
curl -s -D - -H "Cookie: session=$sid" "$url"
echo "\$ curl -s -D - '$url'"
curl -s -D - "$url"
sleep 0.2
echo "\$ grep '$q' /results/access.log"
grep "$q" /results/access.log | mask
echo "\$ grep -a -e '^KEY:' -e '<!-- for:' (cache file whose key has $q)"
for f in $(grep -rl -a "$q" /var/cache/nginx/pages); do
  echo "$f"
  grep -a -e '^KEY:' -e '<!-- for:' "$f" | mask
done
echo "[end]"
