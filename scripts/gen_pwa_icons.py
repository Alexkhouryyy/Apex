"""Export the approved refined double-chevron identity for web and desktop.

The same normalized geometry produces SVG, PNG and ICO assets. Run from any
folder with: python scripts/gen_pwa_icons.py
"""
from pathlib import Path
import math
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dashboard/static/icons'
CYAN, PURPLE = (102, 204, 255), (138, 124, 255)
# Wider upper chevron, lower at 78% width; both inside the maskable safe circle.
CHEVRONS = (
    ((.5,.22),(.83,.557),(.72,.557),(.5,.387),(.28,.557),(.17,.557)),
    ((.5,.52),(.7574,.77),(.65,.77),(.5,.663),(.35,.77),(.2426,.77)),
)

def contour(vertices):
    """Tiny corner radii, sampled identically for vector and raster exports."""
    points = []
    for i, (x,y) in enumerate(vertices):
        prev, nxt = vertices[i-1], vertices[(i+1)%len(vertices)]
        def toward(p):
            distance = math.hypot(p[0]-x,p[1]-y)
            amount = min(.006/distance, .15)
            return (x+(p[0]-x)*amount, y+(p[1]-y)*amount)
        a,b = toward(prev),toward(nxt)
        for step in range(7):
            t=step/6
            points.append(((1-t)**2*a[0]+2*(1-t)*t*x+t*t*b[0],
                           (1-t)**2*a[1]+2*(1-t)*t*y+t*t*b[1]))
    return points

CONTOURS = tuple(contour(shape) for shape in CHEVRONS)

def svg():
    polygons='\n'.join('<polygon points="'+ ' '.join(f'{x*64:.4f},{y*64:.4f}' for x,y in shape)+'"/>' for shape in CONTOURS)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <radialGradient id="bg"><stop stop-color="#11161f"/><stop offset="1" stop-color="#070b14"/></radialGradient>
    <linearGradient id="mark" x1="0" y1="14.08" x2="0" y2="49.28" gradientUnits="userSpaceOnUse"><stop stop-color="#66ccff"/><stop offset="1" stop-color="#8a7cff"/></linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="url(#bg)"/>
  <g fill="url(#mark)">{polygons}</g>
</svg>
'''

def make_icon(size, maskable=False):
    ss=size*4
    bg=Image.new('RGBA',(ss,ss))
    px=bg.load()
    for y in range(ss):
        for x in range(ss):
            t=min(1,math.hypot(x-ss/2,y-ss/2)/(ss/2))
            px[x,y]=tuple(round(a*(1-t)+b*t) for a,b in zip((17,22,31),(7,11,20)))+(255,)
    if not maskable:
        tile=Image.new('L',(ss,ss))
        ImageDraw.Draw(tile).rounded_rectangle((0,0,ss-1,ss-1),radius=ss*14/64,fill=255)
        bg.putalpha(tile)
    mask=Image.new('L',(ss,ss))
    draw=ImageDraw.Draw(mask)
    for shape in CONTOURS:
        draw.polygon([(x*ss,y*ss) for x,y in shape],fill=255)
    gradient=Image.new('RGBA',(ss,ss))
    grad=ImageDraw.Draw(gradient)
    for y in range(ss):
        t=max(0,min(1,(y/ss-.22)/.55))
        color=tuple(round(a*(1-t)+b*t) for a,b in zip(CYAN,PURPLE))+(255,)
        grad.line((0,y,ss,y),fill=color)
    bg.paste(gradient,(0,0),mask)
    return bg.resize((size,size),Image.Resampling.LANCZOS)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for name in ('apex-mark.svg','apex-refined.svg'):
        (OUT/name).write_text(svg(),encoding='utf-8')
    for name,size,maskable in (
        ('icon-192.png',192,False),('icon-512.png',512,False),
        ('icon-maskable-192.png',192,True),('icon-maskable-512.png',512,True),
        ('apple-touch-icon.png',180,True),('favicon-64.png',64,False)):
        make_icon(size,maskable).save(OUT/name)
        print('wrote',name)
    make_icon(256).save(OUT/'apex.ico',sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
    for size in (16,48,128):
        make_icon(size).save(ROOT/f'apex-extension/icons/icon-{size}.png')

if __name__=='__main__':
    main()
