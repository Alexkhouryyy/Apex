"""Read-only readiness for the readers in Apex's pinned NOMAD source."""
from __future__ import annotations

import asyncio
import re
from xml.etree import ElementTree as ET

import httpx

BASE = 'http://127.0.0.1:8080'
READERS = {
    'library': ('Information library', 'nomad_kiwix_server'),
    'courses': ('Courses', 'nomad_kolibri_2'),
    'notes': ('Notes', 'nomad_flatnotes'),
    'tools': ('Data tools', 'nomad_cyberchef'),
}
CATALOG_PATH = '/catalog/v2/entries?count=50&start=0'


def installed(row):
    # getServices builds plain objects, so MySQL's tinyint can remain numeric
    # even though the model's own serialization converts it to a boolean.
    value = row.get('installed')
    return value is True or isinstance(value, int) and value == 1


def local_reader_url(location):
    # NOMAD stores a host port for these readers and preserves owner remaps.
    # Never follow a custom external URL or send owner credentials to a reader.
    port = str(location or '')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        return None
    return f'http://127.0.0.1:{int(port)}'


def catalog_entries(content):
    if len(content) > 1024 * 1024:
        raise ValueError('Reader catalog is too large.')
    try:
        feed = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError('Reader did not return a catalog.') from exc
    atom = '{http://www.w3.org/2005/Atom}'
    if feed.tag != atom + 'feed':
        raise ValueError('Reader did not return a catalog.')
    return sum(bool((row.findtext(atom+'id') or '').strip()) and
               bool((row.findtext(atom+'title') or '').strip())
               for row in feed.findall(atom+'entry'))


async def status(client, nomad_ready):
    rows = None
    if nomad_ready:
        try:
            response = await client.get(BASE+'/api/system/services')
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and all(isinstance(row, dict) for row in data):
                rows = {row.get('service_name'): row for row in data}
        except (httpx.HTTPError, ValueError, TypeError):
            pass

    async def check(key, label, name):
        result = dict(id=key, label=label, installed=False, ready=False,
                      content_ready=False, url=None, state='unknown', catalog_entries=None)
        if not nomad_ready:
            result['state'] = 'nomad_unavailable'
            return result
        if rows is None:
            return result
        row = rows.get(name, {})
        result['installed'] = installed(row)
        if not result['installed']:
            result['state'] = 'not_installed'
            return result
        if row.get('status') != 'running':
            result['state'] = 'not_running'
            return result
        url = local_reader_url(row.get('ui_location'))
        if not url:
            result['state'] = 'address_unavailable'
            return result
        result['url'] = url
        try:
            response = await client.get(url + (CATALOG_PATH if key == 'library' else '/'))
            response.raise_for_status()
            if key == 'library':
                count = catalog_entries(response.content)
                result.update(ready=True, content_ready=count > 0, catalog_entries=count,
                              state='available' if count else 'empty')
            else:
                result.update(ready=True, state='app_available')
        except (httpx.HTTPError, ValueError):
            result['state'] = 'not_answering'
        return result

    return await asyncio.gather(*(check(key, *spec) for key, spec in READERS.items()))
