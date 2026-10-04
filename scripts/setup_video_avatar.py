"""Install the photoreal video avatar on this PC (Windows, NVIDIA GPU).

    .venv\\Scripts\\python scripts\\setup_video_avatar.py           # install, or finish an interrupted install
    .venv\\Scripts\\python scripts\\setup_video_avatar.py --check   # say what is installed

Everything goes in %USERPROFILE%\\apex-video-avatar, outside Apex:
  env\\        its own Python (MuseTalk pins PyTorch 2.0.1 and other versions
              that would clash with Apex's)
  MuseTalk\\   TMElyralab/MuseTalk at the pinned commit below (MIT)
  MuseTalk\\models\\  the weights, from Hugging Face itself. MuseTalk's own
              Windows script downloads through an unofficial mirror
              (hf-mirror.com); this one does not.

Each step is skipped when it is already done, so running it again finishes an
interrupted install. Steps follow MuseTalk's own README (Installation).
About 10 GB, and 20-40 minutes on a home connection.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home() / 'apex-video-avatar'
REPO = 'https://github.com/TMElyralab/MuseTalk.git'
COMMIT = '0a89dec45a0192b824e3cf4daf96c239440c5ed8'          # reviewed 2026-10-04 (docs/OPEN_SOURCE_REGISTER.md)
TORCH = ['torch==2.0.1', 'torchvision==0.15.2', 'torchaudio==2.0.2', '--index-url', 'https://download.pytorch.org/whl/cu118']
MMLAB = ['mmengine', 'mmcv==2.0.1', 'mmdet==3.1.0', 'mmpose==1.1.0']
SERVER = ['av>=12', 'fastapi', 'uvicorn']                       # what scripts/avatar_server.py adds on top
# (repo, files, folder under models) -- the same files MuseTalk's download script fetches
WEIGHTS = [
    ('TMElyralab/MuseTalk', ['musetalkV15/musetalk.json', 'musetalkV15/unet.pth'], ''),   # MuseTalk 1.5, what the server uses
    ('stabilityai/sd-vae-ft-mse', ['config.json', 'diffusion_pytorch_model.bin'], 'sd-vae'),
    ('openai/whisper-tiny', ['config.json', 'pytorch_model.bin', 'preprocessor_config.json'], 'whisper'),
    ('yzd-v/DWPose', ['dw-ll_ucoco_384.pth'], 'dwpose'),
    ('ByteDance/LatentSync', ['latentsync_syncnet.pt'], 'syncnet'),
    ('ManyOtherFunctions/face-parse-bisent', ['79999_iter.pth', 'resnet18-5c106cde.pth'], 'face-parse-bisent'),
]


def env_python(home=HOME):
    return home / 'env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def base_python():
    """A Python MuseTalk supports (3.10 recommended, 3.11 works with its pins)."""
    if os.name == 'nt' and shutil.which('py'):
        for version in ('3.10', '3.11'):
            if subprocess.run(['py', f'-{version}', '-c', 'pass'], capture_output=True).returncode == 0:
                return ['py', f'-{version}']
    for name in ('python3.10', 'python3.11'):
        if shutil.which(name):
            return [name]
    return None


def status(home=HOME):
    """What is in place, step by step, for --check and for skipping steps."""
    musetalk = home / 'MuseTalk'
    marker = home / 'env' / '.apex-installed'
    weights = [musetalk / 'models' / (folder or '') / f for repo, files, folder in WEIGHTS for f in files]
    return {
        'python': env_python(home).exists(),
        'musetalk': (musetalk / 'musetalk').is_dir(),
        'packages': marker.exists() and marker.read_text().strip() == COMMIT,
        'weights': all(w.exists() for w in weights),
        'missing_weights': [str(w.relative_to(musetalk)) for w in weights if not w.exists()],
    }


def run(cmd, **kw):
    print('  $ ' + ' '.join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kw)


def install(home=HOME):
    state = status(home)
    py = env_python(home)
    home.mkdir(parents=True, exist_ok=True)
    if not state['python']:
        base = base_python()
        if not base:
            raise SystemExit('Install Python 3.10 (python.org, tick "py launcher"), then run this again.')
        print('1/4  Creating the avatar\'s own Python...', flush=True)
        run(base + ['-m', 'venv', home / 'env'])
    if not state['musetalk']:
        if not shutil.which('git'):
            raise SystemExit('Install Git for Windows (git-scm.com), then run this again.')
        print(f'2/4  Downloading MuseTalk {COMMIT[:8]}...', flush=True)
        run(['git', 'clone', REPO, home / 'MuseTalk'])
        run(['git', '-C', home / 'MuseTalk', 'checkout', '--quiet', COMMIT])
    if not state['packages']:
        print('3/4  Installing PyTorch (CUDA 11.8), MuseTalk\'s packages and OpenMMLab (the longest step)...', flush=True)
        run([py, '-m', 'pip', 'install', '--upgrade', 'pip'])
        run([py, '-m', 'pip', 'install'] + TORCH)
        run([py, '-m', 'pip', 'install', '-r', home / 'MuseTalk' / 'requirements.txt'])
        run([py, '-m', 'pip', 'install', '--no-cache-dir', '-U', 'openmim'])
        for package in MMLAB:
            run([py, '-m', 'mim', 'install', package])
        run([py, '-m', 'pip', 'install', 'huggingface_hub'] + SERVER)
        (home / 'env' / '.apex-installed').write_text(COMMIT)
    if not status(home)['weights']:
        print('4/4  Downloading the weights from Hugging Face...', flush=True)
        script = ('import os, sys\n'
                  'os.environ.pop("HF_ENDPOINT", None)                 # the official hub, never a mirror\n'
                  'from huggingface_hub import hf_hub_download\n'
                  'repo, folder, files = sys.argv[1], sys.argv[2], sys.argv[3:]\n'
                  'for f in files: hf_hub_download(repo, f, local_dir=folder)\n')
        for repo, files, folder in WEIGHTS:
            run([py, '-c', script, repo, home / 'MuseTalk' / 'models' / folder] + files)
    final = status(home)
    if not all(final[k] for k in ('python', 'musetalk', 'packages', 'weights')):
        raise SystemExit(f'Not complete: {final}')
    print('\nInstalled. Next: make the character\'s idle video (docs/VIDEO_AVATAR.md), then Start-Apex-Video-Avatar.cmd.')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--check', action='store_true')
    a = p.parse_args(argv)
    if a.check:
        state = status()
        for key in ('python', 'musetalk', 'packages', 'weights'):
            print(f"[{'ok  ' if state[key] else 'todo'}] {key}")
        if state['missing_weights'] and state['musetalk']:
            print('      missing: ' + ', '.join(state['missing_weights'][:6]))
        idle = HOME / 'apex-idle.mp4'
        print(f"[{'ok  ' if idle.exists() else 'todo'}] idle video at {idle}")
        return 0 if all(state[k] for k in ('python', 'musetalk', 'packages', 'weights')) else 1
    try:
        install()
    except subprocess.CalledProcessError as exc:
        print(f'\nA step failed (exit {exc.returncode}). Fix what it printed above, then run this again: finished steps are skipped.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
