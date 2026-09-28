"""Filter watcher events before they enter awareness or durable perception."""
import fnmatch
from pathlib import PurePosixPath
import time

DEFAULT_IGNORES = (
    '**/.git/**', '**/.wrangler/**', '**/node_modules/**', '**/.venv/**',
    '**/venv/**', '**/__pycache__/**', '**/.pytest_cache/**', '**/.mypy_cache/**',
    '**/.next/**', '**/dist/**', '**/build/**', '**/*.pyc', '**/*.lock',
    '**/package-lock.json', '**/npm-shrinkwrap.json', '**/yarn.lock',
    '**/pnpm-lock.yaml', '**/bun.lockb', '**/uv.lock', '**/poetry.lock',
    '**/.DS_Store', '**/Thumbs.db', '**/*~', '**/*.swp',
)


def ignored(path, patterns):
    # watchdog emits Windows paths on Windows; glob matching is consistently
    # slash-separated and case-insensitive for those paths, including moves.
    value = str(path).replace('\\', '/').casefold()
    parts = PurePosixPath(value).parts
    candidates = ['/'.join(parts[i:]) for i in range(len(parts))]
    for pattern in patterns:
        p = pattern.strip().replace('\\', '/').casefold()
        if not p:
            continue
        options = [p, p[3:]] if p.startswith('**/') else [p]
        if any(fnmatch.fnmatchcase(c, opt) for c in candidates for opt in options):
            return True
    return False


class FileEvents:
    def __init__(self, log, patterns=DEFAULT_IGNORES, clock=time.monotonic):
        self.log, self.patterns, self.clock = log, tuple(patterns), clock
        self.last = {}

    def handle(self, event):
        if event.is_directory or event.event_type not in ('created', 'modified', 'deleted', 'moved'):
            return
        source = event.src_path
        target = getattr(event, 'dest_path', '')
        source_ok = not ignored(source, self.patterns)
        target_ok = bool(target) and not ignored(target, self.patterns)
        if event.event_type == 'moved':
            if not source_ok and not target_ok:
                return
            text = (f'Moved: {source} → {target}' if source_ok and target_ok
                    else f'Created: {target}' if target_ok else f'Deleted: {source}')
        elif source_ok:
            text = f'{event.event_type.capitalize()}: {source}'
        else:
            return
        now = self.clock()
        key = text.casefold()
        if now-self.last.get(key, -float('inf')) < 1.0:
            return
        self.last = {k:t for k,t in self.last.items() if now-t < 1.0}
        self.last[key] = now
        self.log.add('file', text)
