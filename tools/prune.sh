#!/usr/bin/env bash
# Apply a show's retention (show.json retention_days) without publishing: removes old feed items + their mp3s from
# the site, validates, commits, pushes. Never rewrites history or force-pushes. Local episode folders are untouched.
# usage: prune.sh SHOW_DIR          exit codes as publish.sh (5 no config, 6 lock busy, 1 failure)
set -euo pipefail
trap 'echo "FAILED: prune.sh line $LINENO" >&2' ERR
KIT=$(cd "$(dirname "$0")" && pwd); SHOW=$(cd "$1" && pwd); CONF=$SHOW/config.env
[ -f "$CONF" ] || { echo "FAILED: no $CONF" >&2; exit 5; }
source "$CONF"; BRANCH=${BRANCH:-main}; LOCK_FILE=${LOCK_FILE:-$REPO_DIR/.git/podcast-kit.lock}
REMOTE=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)
if [[ "$REMOTE" == *github.com* ]]; then gh auth status >/dev/null 2>&1 || { echo "FAILED: gh not authenticated" >&2; exit 3; }; fi
exec 9>"$LOCK_FILE"; flock -w 900 9 || { echo "FAILED: publish lock busy" >&2; exit 6; }
SITE=$REPO_DIR/$SECRET; cd "$REPO_DIR"
[ -z "$(git status --porcelain)" ] || { echo "FAILED: $REPO_DIR has uncommitted changes; inspect with git status (nothing was changed)" >&2; exit 1; }
[ -n "$REMOTE" ] && git pull -q --rebase origin "$BRANCH"
HEAD0=$(git rev-parse HEAD)
rollback() { git stash push -q --include-untracked -m "podcast-kit rollback $(date +%F_%T)" >/dev/null 2>&1 || true; echo "stashed this run's uncommitted site changes (nothing deleted; see git stash list in REPO_DIR)" >&2; }
trap 'echo "FAILED: $(basename "$0") line $LINENO" >&2; [ "$(git rev-parse HEAD)" = "$HEAD0" ] && rollback' ERR
python3 "$KIT/feed.py" "$SHOW" "$SITE" "$BASE_URL/$SECRET" prune
python3 "$KIT/validate_feed.py" "$SITE/feed.xml" "$SITE"
if git diff --quiet -- "$SECRET/episodes.json"; then   # only lastBuildDate changed -> leave the repo as it was
  git checkout -q -- "$SECRET/feed.xml"; echo "nothing to prune"; exit 0; fi
git add -A; git commit -qm "retention: remove episodes older than retention_days"
[ -n "$REMOTE" ] && git push -q origin "$BRANCH"; echo "pruned + pushed"
