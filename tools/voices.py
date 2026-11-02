#!/usr/bin/env python3
"""edge-tts voice catalogue helper (cached for 7 days in podcast-kit/cache/voices.json).
usage: voices.py LANG [--gender f|m]       e.g. voices.py en-US ; voices.py zh --gender m ; voices.py ja-JP
       voices.py --refresh                  re-download the list
Ordering: exact locale first, then other locales of the same language, then Multilingual voices
(multilingual voices can read other languages but may carry an accent; listen-test is impossible, so prefer native)."""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pk
CACHE = os.path.join(pk.KIT, 'cache', 'voices.json')

def all_voices(refresh=False):
    if not refresh and os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 7 * 86400:
        return json.load(open(CACHE))
    import edge_tts
    v = asyncio.run(edge_tts.list_voices())
    os.makedirs(os.path.dirname(CACHE), exist_ok=True); json.dump(v, open(CACHE, 'w'), indent=0)
    return v

def candidates(lang, gender=None):
    lang = lang.lower(); base = pk.lang_base(lang); out = []
    g = {'f': 'Female', 'm': 'Male'}.get(gender)
    vs = [v for v in all_voices() if not g or v['Gender'] == g]
    exact = [v for v in vs if v['Locale'].lower() == lang]
    same = [v for v in vs if v['Locale'].lower().split('-')[0] == base and v not in exact]
    if base == 'zh':   # zh-HK voices speak Cantonese and zh-CN-<province> voices speak dialects: not Mandarin substitutes
        cant = lang.startswith('zh-hk')
        same = [v for v in same if (v['Locale'].lower().startswith('zh-hk')) == cant and v['Locale'].count('-') == 1]
    def oddball(v):    # child / cartoon / dialect voices go last within their tier
        cats = ' '.join(v.get('VoiceTag', {}).get('ContentCategories', []) + v.get('VoiceTag', {}).get('VoicePersonalities', []))
        return ('Cartoon' in cats or 'Cute' in cats or 'Dialect' in cats)
    exact.sort(key=oddball); same.sort(key=oddball)
    multi = [v for v in vs if 'Multilingual' in v['ShortName'] and v not in exact and v not in same]
    for tier, lst in (('native', exact), ('same-language', same), ('multilingual', multi)):
        for v in lst: out.append(dict(voice=v['ShortName'], gender=v['Gender'][0].lower(), locale=v['Locale'], tier=tier,
                                      personality=', '.join(v.get('VoiceTag', {}).get('VoicePersonalities', [])),
                                      category=', '.join(v.get('VoiceTag', {}).get('ContentCategories', []))))
    return out

def exists(name): return any(v['ShortName'] == name for v in all_voices())

def pick(lang, gender, exclude=()):
    for c in candidates(lang, gender):
        if c['voice'] not in exclude and c['tier'] != 'multilingual': return c['voice']
    for c in candidates(lang, gender):
        if c['voice'] not in exclude: return c['voice']
    return None

def pool(show):
    """voices for undeclared-voice @speaker lines: show.voice_pool or auto (native first, hosts' voices last)."""
    hv = [h['voice'] for h in show['hosts']]
    vp = show.get('voice_pool') or {}
    res = {}
    for g in ('m', 'f'):
        lst = vp.get(g) or [c['voice'] for c in candidates(show['language'], g) if c['tier'] != 'multilingual'][:8]
        res[g] = [v for v in lst if v not in hv] + [v for v in lst if v in hv]
        if not res[g]: res[g] = [c['voice'] for c in candidates(show['language'], g)][:4]
    return res

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument('lang', nargs='?'); ap.add_argument('--gender'); ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()
    if a.refresh: print(len(all_voices(True)), 'voices cached')
    if a.lang:
        for c in candidates(a.lang, a.gender):
            print(f"{c['voice']:42s} {c['gender']}  {c['locale']:8s} {c['tier']:14s} {c['personality']} | {c['category']}")
