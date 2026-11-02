#!/usr/bin/env python3
"""Scaffold a new show from a show.json draft.
usage: new_show.py DRAFT_show.json [--root /workspace/podcasts] [--force] [--skip-music]
Creates ROOT/<slug>/ with show.json (voices resolved), assets/ (cover.jpg + intro/outro/sting/swoosh/bed.wav +
music_meta.json + music_report.json), FORMAT.md (writing-rules template for the bot to fill in), episodes/, cache/, state/.
Hosts with "voice": "auto" (or none) get a native edge-tts voice of their gender for the show language.
--force rebuilds show.json/assets in an existing folder; FORMAT.md is kept if it exists. Hosting is a separate step
(setup_hosting.sh)."""
import argparse, json, os, shutil, string, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pk, voices as V

def resolve_voices(raw):
    used = set()
    for h in raw['hosts']:
        v = h.get('voice')
        if v and v != 'auto':
            if not V.exists(v): pk.die(f'host {h.get("id")}: voice {v} not in edge-tts --list-voices')
            vl = next(x['Locale'] for x in V.all_voices() if x['ShortName'] == v)
            if pk.lang_base(vl) != pk.lang_base(raw['language']) and 'Multilingual' not in v:
                print(f'WARN: host {h["id"]} voice {v} is {vl}, show language is {raw["language"]}', file=sys.stderr)
        else:
            v = V.pick(raw['language'], h.get('gender', 'f'), used)
            if not v: pk.die(f'no edge-tts voice for {raw["language"]} / {h.get("gender")}')
            h['voice'] = v; print(f'host {h["id"]}: auto voice {v}')
        used.add(h['voice'])
    return raw

def format_md(s):
    L, F = s['length'], s['format']
    hosts_inline = ', '.join(f"{h['id']} = {h['name']} ({h['role']})" for h in s['hosts'])
    table = '| id | name | role | voice | gender | default rate/pitch |\n|---|---|---|---|---|---|\n' + '\n'.join(
        f"| {h['id']} | {h['name']} | {h['role']} | {h['voice']} | {h['gender']} | {h['rate']} / {h['pitch']} |" for h in s['hosts'])
    if F.get('segments'):
        seg = '\n'.join(f'{i}. ' + (x if isinstance(x, str) else f"**{x.get('name','')}**: {x.get('what','')}" +
                        (f" (about {x['minutes']} min)" if x.get('minutes') else '')) for i, x in enumerate(F['segments'], 1))
    else:
        two = len(s['hosts']) > 1
        seg = ('_Suggested default; replace with the structure the owner wants._\n'
               '1. Cold open: one line teasing the main story, then [INTRO].\n'
               '2. Welcome and rundown of what is coming' + (' (lead host; co-host says hello).' if two else '.') + '\n'
               '3. Main segments, each opening with [STING]. ' + ('Split them across the hosts: one explains, the other asks the natural follow-up question that the next facts answer.' if two else 'Tell each one as a story: what happened, why, what it means.') + '\n'
               '4. Optional quick items after [SWOOSH], 1-3 sentences each.\n'
               '5. Recap of 3-4 takeaways, sign-off, [OUTRO].')
    ex = [f"{h['id']}: text spoken by {h['name']}" for h in s['hosts'][:2]]
    ex.append(f"{s['hosts'][0]['id']}(-3%): a slower, more serious line")
    sch = s['schedule']
    schedule = ', '.join(x for x in [sch.get('cadence'), ' '.join(sch.get('days') or []),
                                     ('publish by ' + sch['publish_by'] + ' ' + s['timezone']) if sch.get('publish_by') else '', sch.get('notes')] if x)
    src = '\n'.join(f"- **{x.get('name')}** {x.get('url','')} ({x.get('kind','site')}) {x.get('notes','')}".rstrip() for x in s['sources']) or '- (FILL IN: where the material comes from)'
    rules = '\n'.join(f'- {r}' for r in F.get('rules') or []) or '- (FILL IN: the owner\'s preferences, e.g. what to prioritise or skip, how to handle numbers, names, jargon)'
    t = string.Template(open(os.path.join(pk.KIT, 'templates', 'FORMAT.md.tmpl'), encoding='utf-8').read())
    return t.substitute(title=s['title'], language=s['language'], hosts_inline=hosts_inline, hosts_table=table,
                        target_minutes=L['target_minutes'], target_units=int(L['target_minutes'] * L['per_minute']),
                        unit=L['unit'], per_minute=L['per_minute'], max_line_chars=F['max_line_chars'], schedule=schedule or 'on demand',
                        retention=f"{s['retention_days']} days" if s.get('retention_days') else 'keep all episodes',
                        music_style=s['music']['style'], max_stings=F['max_stings'], max_bed_sections=F['max_bed_sections'],
                        segments=seg, tone=F.get('tone') or '(FILL IN: register, energy, humour, how hosts address the listener)',
                        example_lines='\n'.join(f'{l:43s}' for l in ex), rules=rules, sources=src)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('draft'); ap.add_argument('--root', default='/workspace/podcasts')
    ap.add_argument('--force', action='store_true'); ap.add_argument('--skip-music', action='store_true'); a = ap.parse_args()
    raw = json.load(open(a.draft, encoding='utf-8'))
    for k in pk.REQUIRED:
        if not raw.get(k): pk.die('draft missing ' + k)
    raw.setdefault('slug', pk.slugify(raw['title']))
    if raw['slug'] == 'show' and not raw.get('slug_ok'): print('WARN: title has no ASCII letters; set "slug" explicitly', file=sys.stderr)
    d = os.path.join(a.root, raw['slug'])
    if os.path.exists(os.path.join(d, 'show.json')) and not a.force: pk.die(f'{d} already exists (use --force to rebuild assets)')
    raw = resolve_voices(raw)
    for sub in ('assets', 'episodes', 'cache', 'state'): os.makedirs(os.path.join(d, sub), exist_ok=True)
    json.dump(raw, open(os.path.join(d, 'show.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    s = pk.load_show(d)
    import cover; cover.make(s, pk.show_paths(s)['cover'])
    if not a.skip_music:
        import music; m = s['music']
        music.build(m['style'], pk.show_paths(s)['assets'], m.get('transpose', 0), m.get('bpm'), m.get('seed', 1), m.get('files'), s['dir'])
    fm = os.path.join(d, 'FORMAT.md')
    if not os.path.exists(fm): open(fm, 'w', encoding='utf-8').write(format_md(s))
    print(json.dumps(dict(show_dir=d, slug=s['slug'], hosts={h['id']: h['voice'] for h in s['hosts']},
                          next=['fill in FORMAT.md', f'setup_hosting.sh {d} new|existing ... (needs owner OK)',
                                f'write {d}/episodes/DATE-TYPE/script.txt', f'make_episode.sh {d} DATE TYPE "headline" "desc"']),
                     ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
