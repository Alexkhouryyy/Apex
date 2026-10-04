"""English knowledge and worldwide maps, staged before NOMAD/Docker installation.

Large files and partial downloads stay on the library drive. The saved plan pins
versions until explicitly refreshed. Downloaded archives still need NOMAD readers.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent import apocalypse

CATALOG = 'https://library.kiwix.org/catalog/v2/entries'
PLAN_NAME = 'english-worldwide-plan.json'
HEADROOM = 20 * 1024**3


def original_collections():
    manifest = json.loads((ROOT/'integrations/project-nomad/UPSTREAM.json').read_text())
    archive = ROOT/'integrations/project-nomad-source.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise RuntimeError('Pinned NOMAD archive changed; content preparation stopped.')
    with ZipFile(archive) as source:
        return json.loads(source.read('collections/kiwix-categories.json')), manifest['revision']


def english_resources(spec):
    """Resolve comprehensive tiers and their inheritance; deduplicate shared books."""
    selected = {}
    for category in spec['categories']:
        if category.get('language') != 'en':
            continue
        tiers = {t['slug']: t for t in category['tiers']}
        candidates = [t for t in tiers.values() if t['slug'].endswith('-comprehensive')]
        if len(candidates) != 1:
            raise RuntimeError('No unambiguous comprehensive tier for '+category['slug'])
        seen = set()
        def include(slug):
            if slug in seen or slug not in tiers:
                raise RuntimeError('Invalid tier inheritance in '+category['slug'])
            seen.add(slug)
            tier = tiers[slug]
            if tier.get('includesTier'):
                include(tier['includesTier'])
            for resource in tier['resources']:
                selected.setdefault(resource['id'], dict(resource))
        include(candidates[0]['slug'])
    # Choose one complete Wikipedia edition; never fetch all alternative editions.
    selected['wikipedia_en_all_maxi'] = {'id': 'wikipedia_en_all_maxi', 'title': 'Full English Wikipedia with images'}
    for identifier in list(selected):
        if identifier.endswith(('_nopic', '_mini')) and identifier.rsplit('_', 1)[0]+'_maxi' in selected:
            selected.pop(identifier)
    return list(selected.values())


def latest_zim(client, resource):
    identifier = resource['id']
    pattern = re.compile(r'^'+re.escape(identifier)+r'_(\d{4}-\d{2})\.zim$')
    names = [identifier]
    if '_' in identifier:
        names.append(identifier.rsplit('_', 1)[0])
    for name in names:
        response = client.get(CATALOG, params={'name': name, 'count': 50, 'start': 0})
        response.raise_for_status()
        feed = ET.fromstring(response.content)
        matches = []
        for link in feed.iter('{http://www.w3.org/2005/Atom}link'):
            if link.get('type') != 'application/x-zim':
                continue
            url = link.get('href', '').removesuffix('.meta4')
            filename = urlsplit(url).path.rsplit('/', 1)[-1]
            match = pattern.fullmatch(filename)
            if match and urlsplit(url).scheme == 'https' and int(link.get('length', '0')) > 0:
                matches.append((match[1], url, filename, int(link.get('length'))))
        if matches:
            version, url, filename, size = max(matches)
            # Metalink carries authoritative checksums; no archive is labelled
            # verified on the strength of its size alone.
            meta = client.get(url+'.meta4')
            meta.raise_for_status()
            xml = ET.fromstring(meta.content)
            exact_size = xml.find('.//{urn:ietf:params:xml:ns:metalink}size')
            if exact_size is None or int(exact_size.text or '0') <= 0:
                raise RuntimeError('No exact publisher size for '+filename)
            size = int(exact_size.text)
            checksums = {h.get('type'): (h.text or '').strip().lower()
                         for h in xml.iter('{urn:ietf:params:xml:ns:metalink}hash')}
            algorithm = next((a for a in ('sha-256', 'sha-1', 'md5') if checksums.get(a)), None)
            if not algorithm:
                raise RuntimeError('No publisher checksum for '+filename)
            return {'id': identifier, 'title': resource.get('title', identifier), 'url': url,
                    'version': version, 'bytes': size, 'path': 'nomad/storage/zim/'+filename,
                    'hash_algorithm': algorithm.replace('-', ''), 'hash': checksums[algorithm],
                    'status': 'pending'}
    raise RuntimeError('No current Kiwix archive found for '+identifier)


def build_plan(client):
    spec, revision = original_collections()
    jobs, pending = [], []
    for resource in english_resources(spec):
        if resource.get('type') == 'dataset':
            pending.append({'id': resource['id'], 'reason': 'Needs NOMAD database download and ingestion, not a ZIM file.'})
            continue
        print('Resolving '+resource['id'], flush=True)
        try:
            jobs.append(latest_zim(client, resource))
        except (httpx.HTTPError, ET.ParseError, RuntimeError, ValueError) as exc:
            pending.append({'id': resource['id'], 'reason': str(exc)})
    response = client.get('https://build-metadata.protomaps.dev/builds.json')
    response.raise_for_status()
    latest = max(response.json(), key=lambda row: row['key'])
    if not re.fullmatch(r'\d{8}\.pmtiles', latest['key']):
        raise RuntimeError('Unexpected worldwide map filename.')
    jobs.append({'id': 'worldwide-map', 'title': 'Worldwide detailed map',
                 'url': 'https://build.protomaps.com/'+latest['key'], 'version': latest['key'][:8],
                 'bytes': int(latest['size']), 'path': 'nomad/storage/maps/pmtiles/'+latest['key'],
                 'hash_algorithm': 'md5', 'hash': base64.b64decode(latest['md5sum']).hex(), 'status': 'pending'})
    jobs.sort(key=lambda row: row['bytes'])
    pending.extend([
        {'id': 'nomad-readers', 'reason': 'Install/start Docker and NOMAD, then prepare its apps.'},
        {'id': 'map-fonts-styles-basemap', 'reason': 'Prepare local map base assets and overview in NOMAD while online.'},
        {'id': 'english-courses', 'reason': 'Install Kolibri, choose English channels and import their content before disconnecting.'},
        {'id': 'nomad-ai-rag', 'reason': 'Configure NOMAD AI and embedding models. Its default 11434 port conflicts with existing Ollama.'},
    ])
    return {'profile': 'english-worldwide', 'nomad_revision': revision, 'created_at': int(time.time()),
            'jobs': jobs, 'remaining_setup': pending, 'offline_verified': False}


def save_plan(path, plan):
    plan['updated_at'] = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(plan, indent=2)+'\n', encoding='utf-8')
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.1*(attempt+1))


@contextmanager
def preparation_lock(home):
    home.mkdir(parents=True, exist_ok=True)
    with (home/'preparation.lock').open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another library preparation is running. Keep only one download window open.') from exc
        try:
            yield
        finally:
            lock.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def destination(home, job):
    target = (home/job['path']).resolve()
    target.relative_to(home.resolve())
    if target.suffix not in {'.zim', '.pmtiles'} or int(job['bytes']) <= 0:
        raise RuntimeError('Invalid archive destination or size.')
    return target


def valid_archive(path, job):
    if not path.is_file() or path.stat().st_size != job['bytes']:
        return False
    digest = hashlib.new(job['hash_algorithm'], usedforsecurity=False)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == job['hash']


def remaining_bytes(home, jobs):
    total = 0
    for job in jobs:
        target = destination(home, job)
        if target.exists():
            continue
        partial = target.with_suffix(target.suffix+'.part')
        offset = partial.stat().st_size if partial.exists() else 0
        total += max(0, job['bytes']-offset)
    return total


def download_archive(client, home, job, checkpoint):
    target = destination(home, job)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not valid_archive(target, job):
            raise RuntimeError('Existing archive failed its checksum; preserved for review: '+str(target))
        job.update(status='downloaded', downloaded_bytes=job['bytes'], checksum_verified=True)
        checkpoint()
        return
    partial = target.with_suffix(target.suffix+'.part')
    if partial.is_symlink():
        raise RuntimeError('Linked partial files are not supported.')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > job['bytes']:
        raise RuntimeError('Partial archive is larger than expected; preserved: '+str(partial))
    if offset == job['bytes']:
        if not valid_archive(partial, job):
            raise RuntimeError('Partial archive failed checksum; preserved: '+str(partial))
        partial.replace(target)
        job.update(status='downloaded', downloaded_bytes=job['bytes'], checksum_verified=True)
        checkpoint()
        return
    if shutil.disk_usage(home).free < job['bytes']-offset+HEADROOM:
        raise RuntimeError('Not enough space for this archive plus 20 GiB headroom.')
    headers = {'Accept-Encoding': 'identity'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
        if job.get('etag'):
            headers['If-Range'] = job['etag']
    with client.stream('GET', job['url'], headers=headers) as response:
        response.raise_for_status()
        if response.status_code == 206:
            expected = f'bytes {offset}-{job["bytes"]-1}/{job["bytes"]}'
            if response.headers.get('content-range') != expected:
                raise RuntimeError('Mirror returned an unexpected byte range; partial archive preserved.')
            mode = 'ab'
        elif response.status_code == 200:
            offset, mode = 0, 'wb'
        else:
            raise RuntimeError('Mirror returned an unexpected download response.')
        length = response.headers.get('content-length')
        if length is not None and int(length) != job['bytes']-offset:
            raise RuntimeError('Archive length changed; refresh the plan before downloading.')
        if shutil.disk_usage(home).free < job['bytes']-offset+HEADROOM:
            raise RuntimeError('Not enough free space if the mirror restarts this partial download.')
        job.update(status='downloading', downloaded_bytes=offset, etag=response.headers.get('etag'))
        checkpoint()
        last = time.monotonic()
        with partial.open(mode) as stream:
            for chunk in response.iter_bytes(1024*1024):
                if offset+len(chunk) > job['bytes']:
                    raise RuntimeError('Download exceeded its expected size.')
                stream.write(chunk)
                offset += len(chunk)
                if time.monotonic()-last > 10:
                    job['downloaded_bytes'] = offset
                    checkpoint()
                    print(f'{job["title"]}: {offset/job["bytes"]:.1%}', flush=True)
                    last = time.monotonic()
    if not valid_archive(partial, job):
        raise RuntimeError('Archive incomplete or checksum failed. Partial file preserved; nothing marked downloaded.')
    partial.replace(target)
    job.update(status='downloaded', downloaded_bytes=job['bytes'], checksum_verified=True)
    job.pop('error', None)
    checkpoint()
    print('Downloaded and checksum verified: '+job['title'], flush=True)


def select_jobs(jobs, identifiers=None, max_download_gb=None):
    """Choose whole archives within a decimal-GB budget; never start a huge partial."""
    identifiers = set(identifiers or [])
    if identifiers - {row['id'] for row in jobs}:
        raise RuntimeError('Unknown archive selection: '+', '.join(sorted(identifiers - {row['id'] for row in jobs})))
    candidates = [row for row in jobs if not identifiers or row['id'] in identifiers]
    if max_download_gb is None:
        return candidates
    if not math.isfinite(max_download_gb) or max_download_gb <= 0:
        raise RuntimeError('The download limit must be a positive, finite number of GB.')
    budget = int(max_download_gb * 1_000_000_000)
    selected = []
    for row in sorted(candidates, key=lambda row: row['bytes']):
        # Count full sizes, including saved partials, so a server restarting a
        # transfer cannot expand the selection beyond the chosen archive budget.
        size = int(row['bytes'])
        if size <= 0:
            raise RuntimeError('Invalid archive size.')
        if size <= budget:
            selected.append(row)
            budget -= size
    if not selected:
        raise RuntimeError('No complete archive fits this download limit.')
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan-only', action='store_true', help='Resolve current archive versions without downloading them')
    parser.add_argument('--refresh-plan', action='store_true', help='Resolve newer versions; retains old archives and partial files')
    parser.add_argument('--plan-output', type=Path, help='Metadata output location for --plan-only')
    parser.add_argument('--max-download-gb', type=float, help='Limit this batch to whole archives totalling at most this many decimal GB')
    parser.add_argument('--only', action='append', help='Archive ID to select; repeat for multiple archives')
    args = parser.parse_args()
    if apocalypse.enabled():
        raise RuntimeError('Download preparation must run outside the offline session.')
    if args.plan_output and not args.plan_only:
        raise RuntimeError('--plan-output requires --plan-only.')
    home = apocalypse.library_root()
    output = args.plan_output or home/PLAN_NAME
    with preparation_lock(output.parent if args.plan_only else home):
        with httpx.Client(follow_redirects=True, trust_env=False, timeout=httpx.Timeout(120, connect=20)) as client:
            snapshot = ROOT/'integrations/project-nomad/english-worldwide-plan.json'
            if output.exists() and not args.refresh_plan:
                plan = json.loads(output.read_text(encoding='utf-8'))
            elif not args.plan_only and not args.refresh_plan and snapshot.is_file():
                plan = json.loads(snapshot.read_text(encoding='utf-8'))
                print('Starting from the reviewed archive versions. Use --refresh-plan to select newer ones.', flush=True)
            else:
                plan = build_plan(client)
            if plan.get('profile') != 'english-worldwide':
                raise RuntimeError('Unexpected preparation profile.')
            selected = select_jobs(plan['jobs'], args.only, args.max_download_gb)
            save_plan(output, plan)
            total = sum(row['bytes'] for row in plan['jobs'])
            print(f'English + worldwide: {len(plan["jobs"])} archives, {total/1024**3:.1f} GiB. '
                  f'{len(plan["remaining_setup"])} setup/catalog items still need attention.', flush=True)
            if args.plan_only:
                print('Saved metadata only. No model or archive download was started.', flush=True)
                return
            print(f'This batch: {len(selected)} archives, {sum(row["bytes"] for row in selected)/1_000_000_000:.3f} GB. Other archives remain deferred.', flush=True)
            pending = remaining_bytes(home, selected)
            if shutil.disk_usage(home).free < pending+HEADROOM:
                raise RuntimeError('The remaining plan does not fit with 20 GiB headroom. Existing files were kept.')
            failed = False
            for job in selected:
                try:
                    download_archive(client, home, job, lambda: save_plan(output, plan))
                except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                    job.update(status='needs_attention', error=str(exc), checksum_verified=False)
                    save_plan(output, plan)
                    print('Pending '+job['title']+': '+str(exc), flush=True)
                    failed = True
            print('Archives require prepared NOMAD readers. Courses, medical database ingestion, map assets and AI/RAG setup remain visible in the plan.', flush=True)
            if failed:
                raise RuntimeError('Some archives remain pending. Rerun to resume; no files were discarded.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, httpx.HTTPError, KeyboardInterrupt) as exc:
        print('Library preparation stopped: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
