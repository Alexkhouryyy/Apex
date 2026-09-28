"""Prepare or verify Apex connections without printing credential values.

Run from the Apex folder: python -m scripts.setup_mcp prepare
Then: python -m scripts.setup_mcp connect notion
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'status', 'connect'))
    parser.add_argument('server', nargs='?')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / '.env')
    from agent import mcp_catalog as catalog
    if args.action == 'prepare':
        catalog.prepare()
    elif args.action == 'connect':
        if args.server not in catalog.DEFAULT_CONNECTIONS:
            parser.error('Choose: ' + ', '.join(catalog.DEFAULT_CONNECTIONS))
        try:
            print(json.dumps(catalog.install(args.server), indent=2))
        except catalog.InstallRefused as exc:
            print(str(exc))
            return 1
        return 0
    for row in catalog.listing():
        if row['id'] in catalog.DEFAULT_CONNECTIONS:
            state = 'configured; check runtime status after restarting Apex' if row['installed'] else 'setup required' if row['prepared'] else 'not configured'
            print(f"{row['name']}: {state}")
            if row['missing']:
                print('  Missing: ' + ', '.join(row['missing']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
