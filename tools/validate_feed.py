#!/usr/bin/env python3
"""Structural checks Apple Podcasts cares about. usage: validate_feed.py feed.xml [site_dir]   exit 1 on any error."""
import sys, os, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
NS = {'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'}
f = sys.argv[1]; site = sys.argv[2] if len(sys.argv) > 2 else None
ch = ET.parse(f).getroot().find('channel'); errs = []
for tag in ('title', 'description', 'language', 'link'):
    if ch.find(tag) is None or not (ch.find(tag).text or '').strip(): errs.append('channel missing ' + tag)
for tag in ('itunes:image', 'itunes:category', 'itunes:explicit', 'itunes:author'):
    if ch.find(tag, NS) is None: errs.append('channel missing ' + tag)
img = ch.find('itunes:image', NS)
if img is not None and not img.get('href', '').startswith('https://'): errs.append('itunes:image not https')
if site and not os.path.exists(os.path.join(site, 'cover.jpg')): errs.append('cover.jpg missing from site dir')
guids = set()
for it in ch.findall('item'):
    t = it.findtext('title'); enc = it.find('enclosure')
    if not (t or '').strip(): errs.append('item without title')
    if enc is None: errs.append(f'{t}: no enclosure'); continue
    if enc.get('type') != 'audio/mpeg': errs.append(f'{t}: bad type')
    if not enc.get('url', '').startswith('https://'): errs.append(f'{t}: enclosure not https')
    if not enc.get('length', '').isdigit(): errs.append(f'{t}: bad length')
    g = it.findtext('guid'); errs += [f'{t}: dup/missing guid'] if not g or g in guids else []; guids.add(g)
    try: parsedate_to_datetime(it.findtext('pubDate'))
    except Exception: errs.append(f'{t}: bad pubDate')
    if site:
        p = os.path.join(site, 'ep', enc.get('url').rsplit('/', 1)[1])
        if not os.path.exists(p) or os.path.getsize(p) != int(enc.get('length')): errs.append(f'{t}: length mismatch / file missing')
print('OK' if not errs else '\n'.join(errs), f'({len(guids)} items)'); sys.exit(1 if errs else 0)
