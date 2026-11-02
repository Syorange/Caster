#!/usr/bin/env bash
# Check a show's public feed without changing anything: feed.xml is served, and every enclosure (or just one
# episode id) answers a range request with 206 and the same byte size as the local copy in the site dir.
# usage: verify.sh SHOW_DIR [EPISODE_ID]      exit 0 all live | 2 something not live/mismatched | 5 no config
set -uo pipefail
SHOW=$(cd "$1" && pwd); ID=${2:-}; CONF=$SHOW/config.env
[ -f "$CONF" ] || { echo "FAILED: no $CONF" >&2; exit 5; }
source "$CONF"; SITE=$REPO_DIR/$SECRET; FEED="$BASE_URL/$SECRET/feed.xml"; bad=0
code=$(curl -s -m 20 -o /tmp/.pk_feed.$$ -w '%{http_code}' "$FEED"); [ "$code" = 200 ] || { echo "feed: HTTP $code"; rm -f /tmp/.pk_feed.$$; exit 2; }
for f in $(ls "$SITE/ep" 2>/dev/null); do
  [ -n "$ID" ] && [ "$f" != "$ID.mp3" ] && continue
  LEN=$(stat -c %s "$SITE/ep/$f")
  R=$(curl -s -m 20 -o /dev/null -D - -r 0-1 "$BASE_URL/$SECRET/ep/$f" | tr -d '\r' | awk 'tolower($1)=="content-range:"{print $2" "$3}' | sed 's|.*/||')
  grep -q "$f" /tmp/.pk_feed.$$ && inf=yes || inf=no
  if [ "$R" = "$LEN" ] && [ $inf = yes ]; then echo "ok   $f ($LEN bytes)"; else echo "BAD  $f served=$R local=$LEN in_feed=$inf"; bad=1; fi
done
rm -f /tmp/.pk_feed.$$; [ $bad = 0 ] && echo "feed live: $FEED" && exit 0; exit 2
