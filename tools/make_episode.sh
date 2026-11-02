#!/usr/bin/env bash
# Render SHOW_DIR/episodes/DATE-TYPE/script.txt with the show's title template, then publish it.
# usage: make_episode.sh SHOW_DIR DATE TYPE "HEADLINE" "DESC" [LINK]     (TYPE "-" = show default type)
# env: RENDER_ONLY=1 stops after rendering (exit 0). Exit codes: as publish.sh; 1 also = script lint errors.
set -euo pipefail
trap 'echo "FAILED: make_episode.sh line $LINENO" >&2' ERR
KIT=$(cd "$(dirname "$0")" && pwd); SHOW=$(cd "$1" && pwd); DATE=$2; TYPE=$3; HEAD=$4; DESC=${5:-}; LINK=${6:-}
read -r TYPE TITLE < <(KIT_TOOLS="$KIT" python3 - "$SHOW" "$DATE" "$TYPE" "$HEAD" <<'PY'
import sys, os; sys.path.insert(0, os.environ['KIT_TOOLS']); import pk
s = pk.load_show(sys.argv[1]); t, _ = pk.ep_type(s, None if sys.argv[3] == '-' else sys.argv[3])
d = os.path.join(s['dir'], 'episodes'); me = f'{sys.argv[2]}-{t}'; n = 1 + sum(1 for x in (os.listdir(d) if os.path.isdir(d) else []) if x != me and os.path.exists(os.path.join(d, x, 'episode.mp3')))
print(t, pk.episode_title(s, t, sys.argv[2], sys.argv[4], n).replace('\n', ' '))
PY
)
[ -n "${TITLE:-}" ] || { echo "FAILED: could not build title (check show.json)" >&2; exit 1; }
EP=$SHOW/episodes/$DATE-$TYPE; SCRIPT=$EP/script.txt; OUT=$EP/episode.mp3
[ -s "$SCRIPT" ] || { echo "FAILED: missing $SCRIPT" >&2; exit 4; }
python3 "$KIT/render.py" "$SHOW" "$SCRIPT" --check >/dev/null || { python3 "$KIT/render.py" "$SHOW" "$SCRIPT" --check; exit 1; }
python3 "$KIT/render.py" "$SHOW" "$SCRIPT" "$OUT" --title "$TITLE" --date "$DATE" --type "$TYPE" --desc "$DESC"
[ "${RENDER_ONLY:-0}" = 1 ] && { echo "rendered: $OUT"; exit 0; }
exec bash "$KIT/publish.sh" "$SHOW" "$OUT" "$DATE" "$TYPE" "$TITLE" "$DESC" "$LINK"
