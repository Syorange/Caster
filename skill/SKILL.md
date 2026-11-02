---
name: Caster
description: >-
  Use when a user wants their own podcast made for them: they describe the kind
  of show they want (topic, language, length, hosts, sources, schedule) and you
  design it, build it, render episodes with AI voices and music, and publish
  them to a private feed they can subscribe to in Apple Podcasts or any podcast
  app. Also use it to add an episode to such a show, change a show, or fix a
  failed publish.
---
# Caster

The toolkit lives on the shared box at `/workspace/podcast-kit` (read its `README.md` for full schemas and options). Each show is one folder, `/workspace/podcasts/<slug>/`, holding `show.json`, `FORMAT.md`, `config.env`, `assets/`, `episodes/<DATE>-<TYPE>/` and `state/`. Episodes are spoken with edge-tts voices, mixed with synthesised music, and published as a static RSS feed on GitHub Pages under a secret path.

If `/workspace/podcast-kit` is missing (a different box), say so and stop; the kit has to be copied over first.

## 1. Design the show with the user
Ask only what you can't infer, one question at a time; offer sensible defaults.
- What the show is about and its format: news digest, deep dive, interview-style, storytelling, language learning, translation of an existing show, and so on.
- Language, and whether names of people, companies and places stay in their original language.
- Length per episode and how often (daily, weekdays, weekly) and by when it must be ready (for example before a commute).
- Hosts: how many, gender and role (lead, co-host asking questions, narrator). Voices are picked with `python3 tools/voices.py <lang> [--gender f|m]` or `"voice": "auto"`.
- Sources: which sites, feeds, newsletters or paid memberships. Paid logins come from the vault through the signed-in browser; never put credentials in the show folder.
- Tone and taste: what to emphasise and what to skip (for example "stories and logic, few numbers").
- Music style: `news`, `calm`, `upbeat-tech`, `warm-story` or `none`, or the user's own royalty-free files.
- How many days of episodes to keep (`retention_days`; older ones are removed automatically on each publish, which counts as approved once the user sets it).
- How they want to hear about new episodes (a short note in chat, never MP3 attachments unless asked).

## 2. Create the show
1. Write a draft `show.json` from the answers (required: `title, author, description, language, category, hosts[]`; see the README for `length`, `episode.title_template`, `format`, `music`, `audio`, `cover`, `sources`, `schedule`). Examples: `/workspace/podcast-kit/examples/`.
2. `python3 /workspace/podcast-kit/tools/new_show.py draft.json` creates the folder, cover, music assets and a `FORMAT.md` template.
3. Fill in `FORMAT.md` for this show: segment structure, tone, what to include and skip, sourcing rules. This is the per-episode rulebook; future runs read it before writing.

## 3. Hosting (one time per show; needs the user's yes)
1. Check `gh auth status`. If not signed in, use the sign-in flow; never copy a token yourself.
2. Tell the user it creates a public GitHub repo whose files sit under an unguessable path, with search engines and podcast directories blocked, and get their yes.
3. New repo: `bash tools/setup_hosting.sh SHOW_DIR new [REPO]`. Or add to an existing feed repo: `bash tools/setup_hosting.sh SHOW_DIR existing REPO_DIR BASE_URL` (each show gets its own secret folder; if that repo is published by another pipeline, set `LOCK_FILE` in `config.env` to that pipeline's lock).
4. `config.env` (`REPO_DIR`, `BASE_URL`, `SECRET`) is the only thing keeping the feed private. Never paste the feed URL or `SECRET` into a skill, a public place, or anywhere but a private message to the user when they ask to subscribe.

## 4. Make an episode
1. Gather sources per `show.json` and `FORMAT.md`. A scheduled routine run can't drive the browser itself; for logged-in sites, hand off to a bot that dispatches a browser subagent. Check recent scripts so stories aren't repeated.
2. Write the script yourself into `episodes/<DATE>-<TYPE>/script.txt`. Cue syntax: one line per turn, `HostId: text`; voice tweaks only right after the id, like `A(-3%):` or `B(+2%,+1Hz):`; cues on their own lines: `[INTRO] [STING] [SWOOSH] [OUTRO] [BED ON] [BED OFF] [PAUSE n]`.
3. Every fact carries its source. Label analysis as analysis. Never invent quotes, interviews or reactions. For translations, translate faithfully and credit the original with a link and an AI-translation note.
4. Lint: `python3 tools/render.py SHOW_DIR SCRIPT --check` (syntax, speakers, length against target, cue limits). Fix every error.

## 5. Dry run (safe, publishes nothing)
`RENDER_ONLY=1 bash tools/make_episode.sh SHOW_DIR DATE TYPE "Headline" "Description"` renders the MP3 and stops. Check the duration with `ffprobe` against the target. No bot can hear audio: for a new show or new music, ask the user to listen to the first episode.

## 6. Publish and check it went live
- `bash tools/make_episode.sh SHOW_DIR DATE TYPE "Headline" "Description" [LINK]` lints, renders, then publishes (lock, pull, add to feed with retention, validate, commit, push, wait up to 450 s for the MP3 to return HTTP 206 at the right size and for the feed to list it). `TYPE` `-` uses the show's default type.
- Exit 0 means live. Exit 2: pushed but not live yet; don't re-publish, run `bash tools/verify.sh SHOW_DIR [ID]` after 3 minutes. Exit 3: gh not signed in. Exit 4: script or MP3 missing. Exit 5: no `config.env`. Exit 6: publish lock busy, retry later. Exit 1: anything else, including lint errors.
- Publish an MP3 you already rendered: `bash tools/publish.sh SHOW_DIR MP3 DATE TYPE "Title" "Desc" [LINK]`. Retention only: `bash tools/prune.sh SHOW_DIR`.
- Then send the user the short note they asked for (title, key points, source links).

## 7. Make it recurring
Offer a routine that runs steps 4 and 6 on the show's schedule, early enough to finish before the deadline (a 30-minute episode takes about 4 to 5 minutes to render and needs 4 to 6 GB of RAM). Tell it to stay quiet when there's nothing new, and to check by a set time that the episode is live so a missed run gets caught.

## Known pitfalls
- A voice tag anywhere but right after the speaker id gets read aloud; the linter blocks it. Fix the script.
- Automatic transcription skips chunks; prefer an official transcript when the source publishes one, and allow for it appearing hours after the audio.
- edge-tts sometimes fails a voice ("No audio was received") or refuses a line; retry, swap the voice, or lightly reword.
- Paid-site sessions can drop when browsing leaves the user's network; then member articles show only teasers. Use what's visible, say so, and ask the user to sign in again.
- Scheduled runs are occasionally missed; check by a set time and make the episode by hand if needed.
- The CDN can serve a stale file right after a re-publish; that's why publish checks byte size.
- If an episode looks missing in the podcast app, check the feed first; the app may not have refreshed.
- A failed publish or prune stashes its uncommitted site changes with `git stash` instead of deleting them; check `git stash list` in `REPO_DIR` and only drop stashes with the user's yes.
- Git history keeps every MP3, so the repo grows even with retention.
- Covers have no font for Thai or Devanagari yet.

## Never
- Delete episodes beyond the agreed retention, or edit a live episode, without the user's yes.
- Put tokens, passwords, the feed URL or `SECRET` into a skill, `show.json`, or any shared or public place.
