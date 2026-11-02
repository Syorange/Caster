#!/usr/bin/env python3
"""Maintain one show's podcast feed inside a static site folder (SITE_DIR = REPO_DIR/SECRET).
usage: feed.py SHOW_DIR SITE_DIR BASE_URL add EP.mp3 --date YYYY-MM-DD [--type T] --title T [--desc D] [--link URL]
       feed.py SHOW_DIR SITE_DIR BASE_URL build      regenerate feed.xml from episodes.json
       feed.py SHOW_DIR SITE_DIR BASE_URL prune      apply retention without adding
Episode id = DATE-TYPE (TYPE defaults to show.json episode.default_type); re-adding the same id replaces the file
but keeps its guid and original pubDate. Retention: show.json retention_days (0 = keep everything); on every
add/prune, episodes whose pubDate is older are removed from the feed AND their mp3 deleted from SITE_DIR
(git history is not rewritten). Removed ids print as 'pruned: <id>'.
SITE_DIR layout: feed.xml, cover.jpg, episodes.json, robots.txt, ep/<id>.mp3     (BASE_URL = public URL of SITE_DIR)
Private shows (default) get <itunes:block>Yes</itunes:block> + robots Disallow: the secret path is the only access
control, so never print or commit the full feed URL anywhere public."""
import argparse, json, os, shutil, subprocess, uuid, html, sys
from datetime import datetime, timedelta
from email.utils import format_datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pk

def dur(p):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p],
                                capture_output=True, text=True, check=True).stdout)

def hms(s):
    s = int(round(s)); return f'{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}'

def load_eps(site):
    p = os.path.join(site, 'episodes.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else []

def save_eps(site, eps):
    json.dump(eps, open(os.path.join(site, 'episodes.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

def build(show, site, base):
    eps = load_eps(site); e = html.escape; items = []
    label = show['episode'].get('source_link_label') or 'Source'
    expl = 'true' if show['explicit'] else 'false'
    for ep in sorted(eps, key=lambda x: x['pubDate_iso'], reverse=True):
        url = f"{base}/ep/{ep['file']}"
        body = e(ep['desc']).replace('\n', '<br/>') + (f'<br/><br/>{e(label)}: <a href="{e(ep["link"])}">{e(ep["link"])}</a>' if ep.get('link') else '')
        items.append(f"""    <item>
      <title>{e(ep['title'])}</title>
      <description>{e(ep['desc'])}</description>
      <itunes:summary>{e(ep['desc'])}</itunes:summary>
      <content:encoded><![CDATA[{body}]]></content:encoded>
      <enclosure url="{e(url)}" length="{ep['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{e(ep['guid'])}</guid>
      <pubDate>{ep['pubDate']}</pubDate>
      <itunes:duration>{ep['duration']}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>{expl}</itunes:explicit>
      <itunes:image href="{e(base)}/cover.jpg"/>
    </item>""")
    cat = f'<itunes:category text="{e(show["category"])}">' + (f'<itunes:category text="{e(show["subcategory"])}"/>' if show.get('subcategory') else '') + '</itunes:category>'
    owner = f'\n    <itunes:owner><itunes:name>{e(show["author"])}</itunes:name><itunes:email>{e(show["owner_email"])}</itunes:email></itunes:owner>' if show.get('owner_email') else ''
    block = '\n    <itunes:block>Yes</itunes:block>' if show['private'] else ''
    sub = f'\n    <itunes:subtitle>{e(show["subtitle"])}</itunes:subtitle>' if show.get('subtitle') else ''
    now = format_datetime(datetime.now(pk.tz(show)))
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{e(show['title'])}</title>
    <link>{e(show.get('website') or base + '/')}</link>
    <atom:link href="{e(base)}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>{e(show['language'].lower())}</language>
    <description>{e(show['description'])}</description>
    <itunes:summary>{e(show['description'])}</itunes:summary>{sub}
    <itunes:author>{e(show['author'])}</itunes:author>{owner}
    <itunes:image href="{e(base)}/cover.jpg"/>
    {cat}
    <itunes:explicit>{expl}</itunes:explicit>
    <itunes:type>episodic</itunes:type>{block}
    <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    open(os.path.join(site, 'feed.xml'), 'w', encoding='utf-8').write(xml)
    if show['private']: open(os.path.join(site, 'robots.txt'), 'w').write('User-agent: *\nDisallow: /\n')
    print('feed.xml', len(eps), 'episodes')

def retain(show, site, eps, keep_id=None):
    days = float(show.get('retention_days') or 0)
    if days <= 0: return eps
    cut = datetime.now(pk.tz(show)) - timedelta(days=days); keep = []
    for x in eps:
        if x['id'] != keep_id and datetime.fromisoformat(x['pubDate_iso']) < cut:
            try: os.remove(os.path.join(site, 'ep', x['file']))
            except FileNotFoundError: pass
            print('pruned:', x['id'], x['file'], x['pubDate_iso'], x['title'])
        else: keep.append(x)
    return keep

def prune(show, site, base):
    save_eps(site, retain(show, site, load_eps(site))); build(show, site, base)

def add(show, site, base, mp3, date, etype, title, desc, link=None):
    etype, _ = pk.ep_type(show, etype)
    os.makedirs(os.path.join(site, 'ep'), exist_ok=True)
    eps = load_eps(site)
    eid = f'{date}-{etype}'; fn = f'{eid}.mp3'; dst = os.path.join(site, 'ep', fn)
    shutil.copyfile(mp3, dst)
    old = next((x for x in eps if x['id'] == eid), None)
    eps = [x for x in eps if x['id'] != eid]
    pub = datetime.now(pk.tz(show)) if not old else datetime.fromisoformat(old['pubDate_iso'])
    eps.append(dict(id=eid, type=etype, date=date, file=fn, title=title, desc=desc, link=link,
                    bytes=os.path.getsize(dst), duration=hms(dur(dst)),
                    guid=(old or {}).get('guid') or f"{show['slug']}-{eid}-{uuid.uuid4().hex[:8]}",
                    pubDate=format_datetime(pub), pubDate_iso=pub.isoformat()))
    eps = retain(show, site, sorted(eps, key=lambda x: x['pubDate_iso']), keep_id=eid)
    save_eps(site, eps); build(show, site, base)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('show'); ap.add_argument('site'); ap.add_argument('base'); ap.add_argument('cmd', choices=['add', 'build', 'prune'])
    ap.add_argument('mp3', nargs='?'); ap.add_argument('--date'); ap.add_argument('--type')
    ap.add_argument('--title'); ap.add_argument('--desc', default=''); ap.add_argument('--link'); a = ap.parse_args()
    s = pk.load_show(a.show); base = a.base.rstrip('/'); os.makedirs(a.site, exist_ok=True)
    if a.cmd == 'add':
        if not (a.mp3 and a.date and a.title): ap.error('add needs MP3 --date --title')
        add(s, a.site, base, a.mp3, a.date, a.type, a.title, a.desc, a.link)
    elif a.cmd == 'prune': prune(s, a.site, base)
    else: build(s, a.site, base)
