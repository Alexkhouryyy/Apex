# Hardware check

Double-click **`Test-Apex-Hardware.cmd`** in the Apex folder, or run:

```powershell
cd C:\Users\alexk\Apex
.\Test-Apex-Hardware.cmd          # with a short hands-on demo
.\Test-Apex-Hardware.cmd quick    # just measure, nothing to do
```

It measures the hardware Apex depends on and changes nothing:

| Check | What it measures | Why it matters |
|---|---|---|
| Memory | free RAM and free paging file | Celine's voice fails with "paging file is too small" (error 1455) under about 6 GB spare |
| Disks | free space on C: and the library drive | Windows, Docker and the paging file need room on C: |
| GPU | free GPU memory (nvidia-smi) | Celine's voice wants about 4 GB free |
| Displays | each monitor's size and scale | where the Companion bubble lands |
| Screen capture | one capture and how long it took | what Celine sees on F8 / Ctrl+Alt+C |
| Webcam | frames per second and resolution | hand tracking wants 15+ fps |
| Hand detection (demo) | share of frames where your hand was found in 5 s | under 60%: more light, hand closer |
| Microphone | noise level, or your voice level in the demo | silence means muted or blocked in Windows privacy settings |
| Speakers (demo) | plays a tone and asks if you heard it | |
| Celine's voice | how long one short sentence takes, if her server runs | slow means the GPU is shared |
| PC's own voice | the fallback voice is installed (demo: it speaks) | what you hear when Celine's voice is down |
| Apex | is it running; desktop Companion and hotkeys installed | |
| Internet | how fast api.anthropic.com answers | slow means replies start late |

Quit Apex first for the camera and hand checks: its hand tracker holds the
webcam while it runs.

Every run is saved in `%LOCALAPPDATA%\Apex\hardware\` as text and JSON, and the
console lists what changed since the previous run (for example
`camera_fps: 30.0 -> 12.5`), so a change after an update or a driver install
shows up as numbers.

`python -m tools.doctor` is its sibling for software: keys, settings and
services.
