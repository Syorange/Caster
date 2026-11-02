# podcast-kit

This toolkit lets any bot design, produce and host a private podcast. The user describes the show they want:
topic, format, language, length, hosts, sources and schedule. The bot turns that into a `show.json`, scaffolds the
show, writes each episode as a cue script, and the kit renders it with edge-tts voices, original music and
loudness mastering. It then publishes the episode to a private podcast feed on GitHub Pages, ready for Apple
Podcasts or any other podcast app.

Each show gets its own folder, and everything the tools need comes from that show's `show.json` and `config.env`.
Several shows can share one Pages repo, each in its own secret folder, or each show can have its own repo.

Requirements on the box: `python3` with `numpy scipy pillow mutagen edge-tts`, plus `ffmpeg ffprobe git curl flock`,
and `gh` (logged in) for GitHub hosting. Optional: `faster-whisper` for transcript spot checks.

## Layout
```
podcast-kit/
  README.md
  tools/
    pk.py              shared: show.json loading + defaults, titles, length counting, BS.1770 loudness / QA analysis
    voices.py          edge-tts voice catalogue (cached), per-language candidates, auto-pick
    new_show.py        scaffold a show from a show.json draft (voices, cover, music, FORMAT.md)
    cover.py           3000x3000 cover art from title + tagline (style palette + motif)
    music.py           synthesised music/SFX presets, or import user audio; writes QA report
    render.py          cue script -> mastered MP3 (+ --check lint)
    feed.py            episodes.json + feed.xml in a site folder (add / build / prune, retention)
    validate_feed.py   structural feed checks (https enclosures, lengths, guids, itunes tags)
    setup_hosting.sh   ONE-TIME: new GitHub Pages repo, or join an existing one; writes config.env
    make_episode.sh    title template + lint + render + publish, for one episode
    publish.sh         lock, pull --rebase, feed add, validate, commit, push, wait until live
    verify.sh          read-only live check of a show's feed + enclosures
    prune.sh           apply retention without publishing
  templates/FORMAT.md.tmpl   writing-rules template copied into each show
  examples/                  show.json examples (Kaylee's two existing shows, without hosting details)
  cache/voices.json          edge-tts voice list cache (7 days)

/workspace/podcasts/<slug>/        one show (default root; use new_show.py --root to change)
  show.json        the show definition (schema below)
  config.env       hosting, chmod 600: REPO_DIR, BASE_URL, SECRET  (never commit, print or paste it in chat)
  FORMAT.md        the writing rules for this show (generated, then filled in by the bot)
  assets/          cover.jpg, intro/outro/sting/swoosh/bed.wav, music_meta.json (cue points), music_report.json (QA)
  episodes/<DATE>-<TYPE>/   script.txt, sources.md, episode.mp3, episode.mp3.tts.txt (exact TTS input),
                            episode.mp3.report.json (duration, pace, loudness, mix levels, warnings)
  cache/           TTS clip cache (safe to delete)
  state/           free for show-specific state (e.g. which source items were processed)
```

## Workflow for a bot
1. **Design the show with the user.** Fill a draft `show.json` (schema below). Ask the user only about what
   matters to them: topic, language, length, how many hosts, how often, which sources. Choose sensible defaults
   for everything else. To choose voices, run `tools/voices.py <lang> [--gender f|m]`, or put `"voice": "auto"`.
2. **Scaffold the show:** `tools/new_show.py draft.json [--root /workspace/podcasts]`
3. **Fill in `FORMAT.md`:** the segment structure, tone and show-specific rules (the user's preferences).
4. **Set up hosting (one time, externally visible, so get the owner's OK first):**
   `tools/setup_hosting.sh SHOW_DIR new [REPO_NAME]` creates a new public repo, enables Pages and writes config.env.
   `tools/setup_hosting.sh SHOW_DIR existing REPO_DIR BASE_URL` adds the show to an existing Pages repo in a new secret folder.
   The feed URL is `BASE_URL/SECRET/feed.xml`. Give it to the owner privately; they add it in Apple Podcasts under
   Library → … → Follow a Show by URL.
5. **Each episode:**
   - gather sources and write `episodes/<DATE>-<TYPE>/sources.md`
   - write `episodes/<DATE>-<TYPE>/script.txt` following FORMAT.md
   - `tools/render.py SHOW_DIR episodes/<id>/script.txt --check` and fix every error
   - `tools/make_episode.sh SHOW_DIR DATE TYPE "Headline" "Description" [LINK]` renders and publishes
     (`RENDER_ONLY=1` renders only; TYPE `-` = the show's default type)
   - read `episode.mp3.report.json` and report the exit code honestly (see Exit codes)
6. **Later:** if a publish exited 2, run `tools/verify.sh SHOW_DIR [ID]`. Publishing applies retention
   automatically; `tools/prune.sh SHOW_DIR` applies it without publishing.
7. **Recurring shows:** create a routine that runs steps 5-6 on the schedule in `show.json`.

## Commands
| Step | Command |
|---|---|
| list voices | `tools/voices.py en-US --gender f` / `tools/voices.py zh-CN` / `tools/voices.py --refresh` |
| new show | `tools/new_show.py draft.json [--root DIR] [--force] [--skip-music]` |
| rebuild cover | `tools/cover.py SHOW_DIR [--out file.jpg]` |
| rebuild music | `tools/music.py SHOW_DIR` · preview a preset: `tools/music.py --style calm --out /tmp/x` · `tools/music.py --list` |
| setup hosting | `tools/setup_hosting.sh SHOW_DIR new [REPO]` or `... existing REPO_DIR BASE_URL` |
| lint script | `tools/render.py SHOW_DIR script.txt --check` (exit 1 on errors; JSON with length estimate, warnings) |
| render only | `tools/render.py SHOW_DIR script.txt out.mp3 --title T --date YYYY-MM-DD [--type T] [--desc D]` |
| render + publish | `tools/make_episode.sh SHOW_DIR DATE TYPE "Headline" "Description" [LINK]` |
| publish a rendered mp3 | `tools/publish.sh SHOW_DIR MP3 DATE TYPE "Title" "Description" [LINK]` |
| verify live | `tools/verify.sh SHOW_DIR [DATE-TYPE]` |
| prune | `tools/prune.sh SHOW_DIR` |
| feed by hand | `tools/feed.py SHOW_DIR SITE_DIR BASE_URL add|build|prune ...` · `tools/validate_feed.py feed.xml [SITE_DIR]` |

## show.json schema
Required: `title, author, description, language, category, hosts`. Every other key has the default shown.
Unknown keys (e.g. `_notes`) are ignored.

| key | type / default | meaning |
|---|---|---|
| `slug` | from title | folder name + guid prefix, `[a-z0-9-]`. Set it explicitly for non-Latin titles |
| `title`, `subtitle`, `author`, `description` | str | feed + ID3 + cover. Say in `description` that the show is AI-voiced |
| `language` | BCP-47, e.g. `en-US`, `zh-CN`, `ja-JP` | feed `<language>`, voice selection, length unit, cover font |
| `category`, `subcategory` | Apple category names | e.g. `News`/`Business News`, `Technology`/`Tech News`, `History` |
| `explicit` | false | itunes:explicit |
| `private` | true | adds `<itunes:block>Yes</itunes:block>` + robots `Disallow: /` (keep true for personal feeds) |
| `owner_email`, `website` | null | optional itunes:owner / channel link |
| `timezone` | `UTC` | IANA zone for pubDate and retention |
| `hosts[]` | list | `{id, name, gender m/f, role, voice, rate "+0%", pitch "+0Hz", volume "+0%"}`. `id` is the script label (`A`, `Maya`, `Luke Vargas`). `voice` = edge-tts ShortName or `auto` |
| `voice_pool` | auto | `{"m":[...],"f":[...]}` voices for `@speaker` lines without an explicit voice. Auto = native voices for the language |
| `voice_defaults` | {} | `{"<voice>": {"rate":"+4%","pitch":"+0Hz"}}` default prosody for non-host speakers using that voice |
| `length` | `{target_minutes:15, unit, per_minute, tolerance:0.1}` | unit `chars` (zh/ja/ko/yue) or `words`; defaults: zh 280, ja 330, ko 300 chars/min, others 165 words/min (measured: en-GB +0% 168, en-US +2..4% 186). Calibrate from `measured_pace_per_min` |
| `episode.default_type` | `ep` | TYPE when none is given; episode id = `DATE-TYPE` |
| `episode.title_template` | `{headline}` | fields: `{show} {headline} {date} {date_long} {md_cjk} {year} {month} {day} {weekday} {n}` (n = episode count) |
| `episode.types` | {} | per-type overrides `{"wn-am": {"title_template": "...", "album": "..."}}` |
| `episode.source_link_label` | `Source` | label of the LINK in show notes |
| `retention_days` | 0 | 0 = keep all; otherwise feed items + mp3s older than this are removed on each publish/prune |
| `schedule` | `{cadence, days[], publish_by, notes}` | for the routine and FORMAT.md (the kit does not schedule) |
| `sources[]` | [] | `{name, url, kind rss/site/api/login/local, notes}` |
| `format` | `{segments[], tone, rules[], max_stings 6, max_bed_sections 3, max_line_chars 90 (chars) / 220 (words)}` | goes into FORMAT.md; limits are lint warnings |
| `music` | `{style "news", transpose 0, bpm null, seed 1, files {}, license_note}` | style: `news`, `calm`, `upbeat-tech`, `warm-story`, `none` |
| `music.files` | {} | per asset `intro/outro/sting/swoosh/bed`: path, or `{path, cue_point, max_seconds, fade_out, presence_cut_db}` |
| `audio` | see below | mastering + mix levels |
| `cover` | `{tagline, palette {bg_top,bg_bottom,accent,text: [r,g,b]}, motif chart/waves/grid/sun/bars}` | cover art |

`audio` defaults: `channels 1, bitrate "96k", lufs -16, true_peak -1.5, music_lu -3, sting_lu -4, swoosh_lu -9,
bed_under_voice_lu -26, bed_in_gaps_lu -17, music_duck_db 14, turn_gap_s 0.32, same_speaker_gap_s 0.42`.
All `*_lu` values are loudness relative to the voice. Use `channels 2` with `128k` if you want the music's stereo
width (speech is always centred).

Example (English weekly two-host tech show):
```json
{
  "slug": "patch-notes-weekly",
  "title": "Patch Notes Weekly",
  "subtitle": "The week in tech, explained in 20 minutes",
  "author": "Patch Notes Weekly",
  "description": "A private weekly two-host roundup of the biggest stories in technology. AI-written and AI-voiced from public reporting; for personal listening only.",
  "language": "en-US", "category": "Technology", "subcategory": "Tech News",
  "timezone": "America/Los_Angeles",
  "hosts": [
    {"id": "A", "name": "Maya", "gender": "f", "role": "lead host", "voice": "auto", "rate": "+4%"},
    {"id": "B", "name": "Leo", "gender": "m", "role": "co-host, asks the follow-ups", "voice": "en-US-AndrewNeural", "rate": "+2%"}
  ],
  "length": {"target_minutes": 20, "per_minute": 185},
  "episode": {"default_type": "weekly", "title_template": "{show} #{n}: {headline}"},
  "retention_days": 30,
  "schedule": {"cadence": "weekly", "days": ["Fri"], "publish_by": "06:00"},
  "sources": [{"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "kind": "rss"}],
  "format": {"tone": "Curious, friendly, a little nerdy; explain jargon in one plain sentence.", "max_stings": 5},
  "music": {"style": "upbeat-tech"},
  "audio": {"channels": 2, "bitrate": "128k"},
  "cover": {"tagline": "EVERY FRIDAY · TECH, EXPLAINED"}
}
```
More examples: `examples/kaylee-briefing.json` (two-host Mandarin weekday market briefing) and
`examples/kaylee-whats-news.json` (a Mandarin translation of WSJ What's News with named hosts, a voice pool and
per-edition episode types).

`config.env` (written by setup_hosting.sh, chmod 600):
```
REPO_DIR=/workspace/podcast-pages/<repo>     # local checkout of the Pages repo
BASE_URL=https://<owner>.github.io/<repo>
SECRET=<random 24-char path segment>         # the only access control: keep private
# optional: BRANCH=main   LOCK_FILE=<path>   (set LOCK_FILE to another tool's lock if that tool also publishes to this repo)
```

## Cue script syntax
UTF-8, one cue per line. Blank lines and lines starting with `#` are ignored.
```
A: text                        line by host "A" (voice + default prosody from show.json)
A(-3%): text                   rate override (serious / caveat)
A(+2%,+1Hz): text?             rate + pitch (light question)
A(+0%,+0Hz,-10%): text         rate, pitch, volume
@speaker Jane Doe | f | guest                    extra voice, auto-picked from voice_pool for the language
@speaker Clip 1 | m | clip | en-GB-RyanNeural    extra voice with an explicit edge-tts voice
Jane Doe: text                 line by a declared speaker (re-declaring a show.json host keeps its voice)
[INTRO]                        theme; the next line starts at the intro's cue point (where it resolves), music ducks
[OUTRO]                        closing theme, after the last line
[STING]                        musical segment transition
[SWOOSH]                       lighter whoosh transition
[BED ON] ... [BED OFF]         music bed under the lines in between (2.5 s equal-power fades)
[PAUSE 0.8]                    silence, seconds
```
Speaker names are 1-40 characters with no `: [ ] ( ) # @`. Both ASCII and full-width colons, brackets and commas work.
**Stray tag guard:** a prosody tag that ended up inside the text (e.g. `…？(+2%,+1Hz)` or `（+2%，+1Hz）`) is applied if
the line had none, then removed. A final scrub drops leftover signed Hz/percent fragments, and the render
refuses to run if anything tag-like would still be spoken. Ordinary numbers (`100%`, `涨了2%`) are kept.
`episode.mp3.tts.txt` records exactly what went to TTS.

## Audio pipeline (render.py)
1. TTS per line with edge-tts (6 at a time, 5 retries, cached by content hash).
2. Silence is trimmed from each clip and 8 ms raised-cosine fades go on both edges, so clip boundaries can't click.
   Speakers are level-matched (edge-tts voices differ by up to ~7 dB), then each line is gently evened out.
3. Lines are placed with turn gaps of 0.32 s (speaker change) and 0.42 s (same speaker). Music cues start at their
   cue points from `music_meta.json`.
4. Gain staging in LUFS (BS.1770): the voice stem is set to -18 LUFS, music cues to `music_lu`, the bed sits at
   `bed_under_voice_lu` under speech and `bed_in_gaps_lu` in gaps longer than about 0.6 s.
5. Side-chain ducking from the voice envelope. Bed: 150 ms look-ahead, 0.6 s hold, 80 ms attack, 0.6 s release.
   Music cues: 120 ms look-ahead, 0.15 s hold, 50 ms attack, 0.35 s release, ducked by `music_duck_db`. The bed
   starts dipping before a word starts and doesn't pump between sentences.
6. Mastering: gain to `lufs`, then a look-ahead true-peak limiter (4x oversampled peak detection, ceiling 0.5 dB
   below `true_peak`), then MP3. The MP3 itself is then measured with ffmpeg `ebur128` and re-encoded, up to 3
   times, until |I − target| ≤ 0.5 LU and TP ≤ `true_peak`. (The older two-pass `loudnorm` let a test file reach
   +1.1 dBTP, so it was replaced.)
7. ID3 tags (title, author, album = show or type, date, language, comment) plus the cover.
8. `episode.mp3.report.json` holds the duration, line count, length units, `measured_pace_per_min`, the measured
   `bed_under_voice_rel_lu` / `bed_in_gaps_rel_lu` / `music_cues_unducked_rel_lu`, the final integrated LUFS,
   LRA and true peak, and the limiter gain reduction.

## Music and SFX (music.py)
Everything is synthesised with numpy/scipy (no samples, so no third-party rights). Each style writes
`intro` (a cadence that resolves to the tonic), `outro` (cadence plus a long natural decay), `sting` (a 3-4 note
motif onto a tonic chord hit), `swoosh` (a band-pass noise sweep with a pan sweep) and `bed` (a seamless loop).

| style | key / tempo | instruments | intro → outro cadence |
|---|---|---|---|
| `news` | D minor, 112 BPM | FM electric piano motif + octave double, detuned saw pad, pulse bass, kick/tick/hat clock | Dm–B♭–Gm–A7sus4–A–**Dm** / B♭–C–Gm–A–**Dm** |
| `calm` | F major (Lydian colour), 70 BPM | FM celesta/bells, soft dark pad, sub bass, no drums, 3 s reverb | Fmaj7–Dm7–B♭maj7–C7sus4–C–**Fmaj9** / B♭maj7–Gm7–C7sus4–C–**Fmaj7** |
| `upbeat-tech` | A minor, 122 BPM | saw plucks with an enveloped filter in 16ths, side-chain-pumped pad, four-on-the-floor kick, clap, hats | Am–F–C–G–E–**Am** / F–G–Esus4–E–**Am** |
| `warm-story` | G major, 84 BPM | Karplus-Strong fingerpicked guitar (allpass-tuned within 4 cents), upright bass, shaker | G–Em–C–D–**G** / C–D–Em–D/F♯–**G** |
| `none` | none | no music; cues become short pauses | none |

The craft is objective: chord voicings come from a voice-leading search (smallest total semitone movement, no
semitone clusters), exponential ADSR envelopes, band-limited (polyBLEP) oscillators, panned and detuned layers
for stereo width, and a stereo convolution reverb with decorrelated IRs, early reflections, damping and a wet
path high-passed at 180 Hz. The bed is carved for speech (-5 dB at 2.5 kHz, -3 dB at 280 Hz, low-passed at
9 kHz). Every asset starts with 30 ms of true silence and decays naturally to below -54 dB before a short
raised-cosine fade. Assets also get a 25 Hz high-pass, DC removal, normalisation to -18 LUFS and a true-peak cap
of -1 dBTP. The bed loop is folded and wrapped with a 20 ms equal-power crossfade.
`transpose` (semitones), `bpm` and `seed` give variations.

**User-supplied audio:** set `music.files`, e.g. `{"intro": {"path": "theme.mp3", "cue_point": 5.2}, "bed": "bed.wav"}`.
Any format ffmpeg reads works. Imported files are trimmed of leading/trailing silence, faded, DC-cleaned and
normalised the same way. A bed also gets a seamless-loop crossfade and the presence cut. A relative cue_point is
measured from the asset start; a negative one, from its end. Assets you don't list are still synthesised from
`style`. Only use files the owner has the rights to, and record where they came from in `license_note`.

`assets/music_report.json` reports, for each asset: seconds, LUFS, true peak, sample peak, clipped samples, DC
offset, start/end level (5 ms), share of energy per band (sub <60 Hz / low 60-250 / mid 250-2k / presence 2-6k /
air >6k), spectral centroid, stereo correlation, mono-fold loudness loss, and the loop seam jump (bed).

## Exit codes (publish.sh / make_episode.sh / prune.sh / verify.sh)
| code | meaning | what to do |
|---|---|---|
| 0 | live: the mp3 answers a range request with 206 and the same byte size as the new file, and feed.xml lists it | report the episode |
| 1 | other failure: script lint errors, feed validation, git, or a dirty Pages repo. Changes this run made but didn't commit are rolled back | read stderr and fix |
| 2 | pushed but not live within LIVE_TRIES×LIVE_SLEEP (default 45×10 s) | Pages is slow: run `verify.sh` in a few minutes; don't call it live yet |
| 3 | `gh` not authenticated (GitHub remotes only) | owner must log in (`gh auth login`) |
| 4 | mp3 or script missing/empty | render first |
| 5 | no `config.env` (hosting not set up) | run setup_hosting.sh (needs owner OK) |
| 6 | publish lock busy for 900 s (another publish to the same repo) | retry later |

Safety properties: publishes are serialised per repo with `flock` (lock in `.git/`, or LOCK_FILE). The scripts
always `git pull --rebase` and never force-push. Retention deletes mp3s from the site but never rewrites git
history. Network errors during the live check count as "not live yet". Re-publishing the same id keeps its guid
and pubDate, so podcast apps don't show a duplicate.

## Privacy
- Pages repos on free accounts are public. The random `SECRET` path, `robots.txt Disallow: /`,
  `<itunes:block>Yes</itunes:block>` and no index page are what keep the feed unlisted. They don't make it secret
  from anyone who has the URL.
- Never write the SECRET, the full feed URL, tokens or passwords into show.json, FORMAT.md, scripts, commit
  messages or chat. They live only in `config.env`.
- AI-voiced shows should say so in the description. A translation of someone else's show is for personal use
  only: link the original.

## Limitations
- Nobody can listen during production. The music is checked by measurement only (numbers above) and the speech
  can only be spot-checked by transcription (e.g. faster-whisper). Ask the owner to listen to the first episode
  and the music presets.
- Cover fonts: Noto Sans CJK (Latin + CJK) and DejaVu. Scripts outside those (Thai, Devanagari…) need a font added
  to `cover.py`.
- A 30-minute episode uses about 4-6 GB of RAM while mixing. Edge-tts is a free online service: it can rate-limit
  ("No audio was received"). Render retries 5 times per line.
- Source fetching (RSS, logins, transcripts) is show-specific and not part of the kit. Record it in `sources` and
  FORMAT.md, and keep any helper scripts in the show folder.
