#!/usr/bin/env bash
# ONE-TIME hosting setup for a show. Writes SHOW_DIR/config.env (chmod 600: REPO_DIR, BASE_URL, SECRET).
# The SECRET path segment is the only access control on a public Pages site: keep it out of chats, logs and commits.
#
#   setup_hosting.sh SHOW_DIR new [REPO_NAME]
#       Creates a NEW public GitHub repo under the gh-authenticated account, enables GitHub Pages, clones it to
#       $PAGES_ROOT/REPO_NAME (default /workspace/podcast-pages). Externally visible: needs the owner's OK first.
#   setup_hosting.sh SHOW_DIR existing REPO_DIR BASE_URL
#       Adds the show to an already checked-out Pages repo (shared by several shows), in its own new secret folder.
#       Nothing is pushed until the first publish. If that repo is also published by other tools, set LOCK_FILE in
#       config.env to the lock those tools use so publishes stay serialised.
set -euo pipefail
SHOW=$(cd "$1" && pwd); MODE=$2; CONF=$SHOW/config.env
[ -f "$SHOW/show.json" ] || { echo "no $SHOW/show.json" >&2; exit 4; }
[ -f "$CONF" ] && { echo "$CONF already exists; refusing to overwrite" >&2; exit 1; }
SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(18))")
if [ "$MODE" = new ]; then
  gh auth status >/dev/null 2>&1 || { echo "FAILED: gh not authenticated" >&2; exit 3; }
  OWNER=$(gh api user --jq .login); REPO=${3:-pod-$(python3 -c "import secrets;print(secrets.token_hex(4))")}
  D=${PAGES_ROOT:-/workspace/podcast-pages}/$REPO; [ -e "$D" ] && { echo "$D exists" >&2; exit 1; }
  gh repo create "$OWNER/$REPO" --public --description "personal" --clone=false
  mkdir -p "$D/$SECRET"; cd "$D"; git init -q -b main
  git config user.name "$OWNER"; git config user.email "$(gh api user --jq .id)+$OWNER@users.noreply.github.com"
  printf 'User-agent: *\nDisallow: /\n' > robots.txt; touch .nojekyll; echo '<!doctype html><title>.</title>' > index.html
  cp "$SHOW/assets/cover.jpg" "$SECRET/"
  git add -A; git commit -qm init; git remote add origin "https://github.com/$OWNER/$REPO.git"; git push -q -u origin main
  gh api -X POST "repos/$OWNER/$REPO/pages" -f 'source[branch]=main' -f 'source[path]=/' >/dev/null
  BASE="https://$OWNER.github.io/$REPO"
elif [ "$MODE" = existing ]; then
  D=$(cd "$3" && pwd); BASE=${4%/}; git -C "$D" rev-parse --git-dir >/dev/null
  [ -f "$D/robots.txt" ] || printf 'User-agent: *\nDisallow: /\n' > "$D/robots.txt"
  mkdir -p "$D/$SECRET"; cp "$SHOW/assets/cover.jpg" "$D/$SECRET/"
else echo "usage: setup_hosting.sh SHOW_DIR new [REPO] | existing REPO_DIR BASE_URL" >&2; exit 1; fi
umask 077; printf 'REPO_DIR=%s\nBASE_URL=%s\nSECRET=%s\n' "$D" "$BASE" "$SECRET" > "$CONF"; chmod 600 "$CONF"
echo "config written: $CONF"
echo "feed URL (live after the first publish) = BASE_URL/SECRET/feed.xml from $CONF; share it only privately"
