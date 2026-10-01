"""Custom voices for the local Qwen voice servers: one folder per voice.

    ~/apex-voices/                (APEX_VOICES_DIR overrides)
        alex/
            reference.wav         20-30 s of the person speaking (wav/flac/ogg/mp3/m4a/webm)
            transcript.txt        exactly the words in the recording
            voice.json            optional: {"name": "ALEX"}

Celine's original recording (``--reference``, ~/Downloads/celine.ogg) still
works with no folder: it is the voice ``celine`` unless a ``celine`` folder
exists. Standard library only, because both the voice servers' environments
and Apex's own import it.

The servers re-read the folder on every request (a directory listing and a few
stats), so a voice saved from Apex's Voices page is usable without a restart.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
AUDIO = ('.wav', '.flac', '.ogg', '.mp3', '.m4a', '.webm')
MAX_TRANSCRIPT = 2000


def voices_dir() -> Path:
    return Path(os.environ.get('APEX_VOICES_DIR') or Path.home() / 'apex-voices')


def slug(name: str) -> str:
    """A folder id from a display name: "Alex" -> "alex", "Céline 2" -> "celine-2"."""
    import unicodedata
    plain = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', plain).strip('-')[:32].strip('-')


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    reference: Path
    transcript: str
    stamp: float            # changes whenever the recording or transcript does
    folder: bool = True     # False for the legacy --reference recording

    def profile(self) -> dict:
        return dict(id=self.id, name=self.name, voice_type='cloned', language='en', default_engine='qwen')


def _read_folder(path: Path) -> Voice | None:
    if not ID.match(path.name):
        return None
    refs = [p for p in sorted(path.iterdir()) if p.stem == 'reference' and p.suffix.lower() in AUDIO and p.is_file()]
    script = path / 'transcript.txt'
    if not refs or not script.is_file():
        return None
    transcript = script.read_text(encoding='utf-8-sig').strip()
    if not transcript or len(transcript) > MAX_TRANSCRIPT:
        return None
    name = path.name.upper()
    meta = path / 'voice.json'
    stamps = [refs[0].stat().st_mtime, script.stat().st_mtime]
    if meta.is_file():
        try:
            given = json.loads(meta.read_text(encoding='utf-8')).get('name')
            if isinstance(given, str) and given.strip():
                name = given.strip()[:40]
            stamps.append(meta.stat().st_mtime)
        except (ValueError, AttributeError):
            pass
    return Voice(path.name, name, refs[0], transcript, max(stamps))


def discover(root: Path | None = None, legacy: Path | None = None, legacy_transcript: str = '') -> list[Voice]:
    """Every usable voice: Celine first, then by name. Broken folders are skipped."""
    root = voices_dir() if root is None else root
    found: dict[str, Voice] = {}
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.is_dir():
                try:
                    voice = _read_folder(path)
                except OSError:
                    voice = None
                if voice:
                    found[voice.id] = voice
    if legacy is not None and 'celine' not in found and legacy.is_file() and legacy_transcript.strip():
        found['celine'] = Voice('celine', 'CELINE', legacy, legacy_transcript.strip(), legacy.stat().st_mtime, folder=False)
    return sorted(found.values(), key=lambda v: (v.id != 'celine', v.name.casefold()))


def choose(voices: list[Voice], wanted: str = '') -> Voice:
    """The voice a request asked for, by id or name; the default (Celine, else
    the first) when it asked for none. ValueError says which voices exist."""
    if not voices:
        raise ValueError(f'No voices yet. Record one on the Voices page, or add a folder to {voices_dir()}.')
    if not wanted:
        return voices[0]
    for v in voices:
        if v.id == wanted or v.name.casefold() == wanted.casefold():
            return v
    raise ValueError(f'No voice called {wanted!r}. Voices: ' + ', '.join(v.name for v in voices))
