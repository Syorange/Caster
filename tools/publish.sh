#!/usr/bin/env bash
# Publish one rendered mp3 into a show's feed on a static git-hosted site (GitHub Pages). Fails loudly.
# usage: publish.sh SHOW_DIR MP3 DATE TYPE "TITLE" "DESC" [LINK]
# exit: 0 live | 1 other failure (validation, git) | 2 pushed but not live yet after wait | 3 gh not authenticated
#       4 mp3 missing/empty | 5 no SHOW_DIR/config.env (hosting not set up) | 6 publish lock busy (900 s)
# config.env: REPO_DIR, BASE_URL, SECRET  [BRANCH=main] [LOCK_FILE=$REPO_DIR/.git/podcast-kit.lock]
# env: LIVE_TRIES (45) x LIVE_SLEEP (10 s) for the live check.
set -euo pipefail
trap 'echo "FAILED: publish.sh line $LINENO" >&2' ERR
KIT=$(cd "$(dirname "$0")" && pwd)
SHOW=$(cd "$1" && pwd); MP3=$2; DATE=$3; TYPE=$4; TITLE=$5; DESC=$6; LINK=${7:-}
CONF=$SHOW/config.env
[ -f "$CONF" ] || { echo "FAILED: no $CONF (run setup_hosting.sh first)" >&2; exit 5; }
source "$CONF"; BRANCH=${BRANCH:-main}; LOCK_FILE=${LOCK_FILE:-$REPO_DIR/.git/podcast-kit.lock}
[ -n "${REPO_DIR:-}" ] && [ -n "${BASE_URL:-}" ] && [ -n "${SECRET:-}" ] || { echo "FAILED: config.env needs REPO_DIR, BASE_URL, SECRET" >&2; exit 5; }
[ -s "$MP3" ] || { echo "FAILED: no mp3 $MP3" >&2; exit 4; }
REMOTE=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)
if [[ "$REMOTE" == *github.com* ]]; then gh auth status >/dev/null 2>&1 || { echo "FAILED: gh not authenticated" >&2; exit 3; }; fi
exec 9>"$LOCK_FILE"; flock -w 900 9 || { echo "FAILED: publish lock busy" >&2; exit 6; }
SITE=$REPO_DIR/$SECRET; mkdir -p "$SITE"; cp -f "$SHOW/assets/cover.jpg" "$SITE/cover.jpg"
cd "$REPO_DIR"
[ -z "$(git status --porcelain)" ] || { echo "FAILED: $REPO_DIR has uncommitted changes; inspect with git status (nothing was changed)" >&2; exit 1; }
[ -n "$REMOTE" ] && git pull -q --rebase origin "$BRANCH"
HEAD0=$(git rev-parse HEAD)
rollback() { git stash push -q --include-untracked -m "podcast-kit rollback $(date +%F_%T)" >/dev/null 2>&1 || true; echo "stashed this run's uncommitted site changes (nothing deleted; see git stash list in REPO_DIR)" >&2; }
trap 'echo "FAILED: $(basename "$0") line $LINENO" >&2; [ "$(git rev-parse HEAD)" = "$HEAD0" ] && rollback' ERR
python3 "$KIT/feed.py" "$SHOW" "$SITE" "$BASE_URL/$SECRET" add "$MP3" --date "$DATE" --type "$TYPE" --title "$TITLE" --desc "$DESC" ${LINK:+--link "$LINK"}
python3 "$KIT/validate_feed.py" "$SITE/feed.xml" "$SITE"
git add -A
git diff --cached --quiet || git commit -qm "episode $DATE-$TYPE"
[ -n "$REMOTE" ] && git push -q origin "$BRANCH"
exec 9>&-   # release the lock before the (slow) live check
URL="$BASE_URL/$SECRET/ep/$DATE-$TYPE.mp3"; LEN=$(stat -c %s "$SITE/ep/$DATE-$TYPE.mp3")
for i in $(seq 1 "${LIVE_TRIES:-45}"); do   # live = 206 AND served size equals the new file (catches stale CDN copies)
  R=$(curl -s -m 20 -o /dev/null -D - -r 0-1 "$URL" | tr -d '\r' | awk 'tolower($1)=="content-range:"{print $2" "$3}' | sed 's|.*/||') || R=""   # network error = not live yet
  [ "$R" = "$LEN" ] && curl -s -m 20 "$BASE_URL/$SECRET/feed.xml" | grep -q "$DATE-$TYPE.mp3" && { echo "live: $URL"; echo "feed: $BASE_URL/$SECRET/feed.xml"; exit 0; }
  sleep "${LIVE_SLEEP:-10}"
done
echo "WARN: pushed but not live after $(( ${LIVE_TRIES:-45} * ${LIVE_SLEEP:-10} ))s: $URL (re-check with verify.sh)" >&2; exit 2
