"""Run on the Apex laptop: verify local Qwen speech and enable Voicebox."""
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv, set_key
load_dotenv(ROOT / '.env')
import config
from voice.voicebox import synthesize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default=None, help='Existing Voicebox profile ID or unique name')
    args = parser.parse_args()
    if args.profile is not None:
        config.VOICEBOX_PROFILE = args.profile
    print('Checking local Voicebox and generating a short Qwen 1.7B test. First load may take a few minutes.')
    try:
        audio = asyncio.run(synthesize('Hey Alex, your local Qwen voice is connected to Apex.'))
    except Exception as exc:
        print(f'Voicebox setup stopped: {exc}')
        return 1
    env = ROOT / '.env'
    if env.exists():
        backup = env.with_name('.env.voicebox-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        shutil.copy2(env, backup)
    set_key(str(env), 'TTS_ENGINE', 'voicebox')
    set_key(str(env), 'VOICEBOX_URL', config.VOICEBOX_URL)
    if args.profile is not None:
        set_key(str(env), 'VOICEBOX_PROFILE', args.profile)
    print(f'PASS: Voicebox returned {len(audio)} bytes of WAV audio. Local speech enabled; brain settings preserved.')
    if sys.platform == 'win32':
        import winsound
        try:
            winsound.PlaySound(audio, winsound.SND_MEMORY)
        except RuntimeError:
            print('Audio generated, but Windows could not play the test. Test playback in the Companion.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
