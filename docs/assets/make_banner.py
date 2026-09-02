"""Generate the Apex repo banner.

Colours are lifted from dashboard/static/styles.css so the banner and the
product are the same thing rather than two designers' guesses:
  --bg #070b14  --text #d7dde8  --text-mute #8892a6
  --accent #6cf  --accent-2 #8a7cff
"""
import math
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG      = (7, 11, 20)
TEXT    = (215, 221, 232)
MUTE    = (136, 146, 166)
ACCENT  = (102, 204, 255)
ACCENT2 = (138, 124, 255)

SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS   = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO   = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# Supersample, then downscale — cheap antialiasing for the arcs and dots.
S = 2
img = Image.new("RGB", (W * S, H * S), BG)
d = ImageDraw.Draw(img, "RGBA")


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# --- backdrop: a slow diagonal wash, brighter toward the upper right --------
for y in range(0, H * S, 2 * S):
    for x in range(0, W * S, 40 * S):
        t = ((x / (W * S)) * 0.6 + (1 - y / (H * S)) * 0.4)
        glow = int(16 * max(0.0, t - 0.35))
        if glow:
            d.rectangle([x, y, x + 40 * S, y + 2 * S],
                        fill=(BG[0] + glow // 2, BG[1] + glow // 2, BG[2] + glow))

# --- the mark: concentric arcs, the dashboard's orb seen edge-on ------------
cx, cy = int(W * 0.795) * S, int(H * 0.5) * S
for i, (r, width, start, end, t) in enumerate([
        (215, 3, -58, 118, 0.00),
        (176, 2, 118, 292, 0.35),
        (137, 4, -20, 145, 0.62),
        (98,  2, 150, 340, 0.85),
]):
    col = lerp(ACCENT, ACCENT2, t)
    box = [cx - r * S, cy - r * S, cx + r * S, cy + r * S]
    d.arc(box, start, end, fill=col + (235,), width=width * S)

# A pinch: two points converging — the gesture the whole spatial half is built on.
for ang, rad, size in ((-34, 137, 9), (26, 137, 9)):
    px = cx + int(math.cos(math.radians(ang)) * rad * S)
    py = cy + int(math.sin(math.radians(ang)) * rad * S)
    d.ellipse([px - size * S, py - size * S, px + size * S, py + size * S],
              fill=ACCENT + (255,))
d.ellipse([cx - 27 * S, cy - 27 * S, cx + 27 * S, cy + 27 * S],
          fill=lerp(ACCENT, ACCENT2, 0.5) + (255,))

# --- wordmark ---------------------------------------------------------------
x0 = 96 * S
title = ImageFont.truetype(SANS_B, 148 * S)
d.text((x0, 196 * S), "APEX", font=title, fill=TEXT)

# Accent rule under the wordmark, fading left to right.
ty = 372 * S
tw = 322 * S
for i in range(tw):
    d.rectangle([x0 + i, ty, x0 + i + 1, ty + 5 * S],
                fill=lerp(ACCENT, ACCENT2, i / tw))

sub = ImageFont.truetype(SANS, 30 * S)
d.text((x0, 404 * S),
       "A self-hosted, voice-first, always-on personal AI agent.",
       font=sub, fill=TEXT)

small = ImageFont.truetype(SANS, 25 * S)
d.text((x0, 448 * S),
       "Your hardware. Your data. Any model.",
       font=small, fill=MUTE)

# --- the honest footer: what it is, in the project's own terms --------------
mono = ImageFont.truetype(MONO, 21 * S)
d.text((x0, 524 * S),
       "one brain  ·  every device  ·  MIT",
       font=mono, fill=MUTE)

img = img.resize((W, H), Image.LANCZOS)
img.save("docs/assets/apex-banner.png", optimize=True)
print("wrote docs/assets/apex-banner.png")
