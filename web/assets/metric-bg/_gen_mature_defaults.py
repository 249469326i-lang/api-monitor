"""Mature cyber / mecha anime-aesthetic pixel loops.
No chibi characters. Dense atmosphere, CRT, city, alert room.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageChops
import os
import math
import random

OUT = os.path.dirname(os.path.abspath(__file__))


def quant(im):
    # Keep more colors for denser look
    return im.convert("P", palette=Image.ADAPTIVE, colors=160)


def save_gif(frames, path, duration=70):
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,
        optimize=False,
    )
    print("wrote", os.path.basename(path), frames[0].size, len(frames), "f", os.path.getsize(path), "B")


def clamp(v, a=0, b=255):
    return max(a, min(b, int(v)))


def put(px, W, H, x, y, rgba):
    if 0 <= x < W and 0 <= y < H:
        px[x, y] = rgba


def blend(a, b, t):
    return tuple(clamp(a[i] * (1 - t) + b[i] * t) for i in range(3)) + (255,)


def vignette(im, strength=0.45):
    W, H = im.size
    px = im.load()
    cx, cy = W / 2, H / 2
    maxd = math.hypot(cx, cy)
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - cx, y - cy) / maxd
            f = 1 - strength * (d ** 1.6)
            r, g, b, a = px[x, y]
            px[x, y] = (clamp(r * f), clamp(g * f), clamp(b * f), a)
    return im


def scanlines(im, alpha=40):
    d = ImageDraw.Draw(im)
    W, H = im.size
    for y in range(0, H, 2):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    return im


def noise(im, amount=10, seed=0):
    rng = random.Random(seed)
    px = im.load()
    W, H = im.size
    for _ in range(W * H // max(1, 80 - amount)):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        r, g, b, a = px[x, y]
        n = rng.randint(-amount, amount)
        px[x, y] = (clamp(r + n), clamp(g + n), clamp(b + n), a)
    return im


def draw_building(d, x, y1, y2, w, frame, bi, palette):
    body, edge, win_on, win_off = palette
    d.rectangle([x, y1, x + w, y2], fill=body, outline=edge)
    # antenna
    if w > 10 and bi % 3 == 0:
        d.line([(x + w // 2, y1 - 8), (x + w // 2, y1)], fill=edge)
        d.point((x + w // 2, y1 - 8), fill=(255, 80, 120, 255))
    # windows grid
    for wy in range(y1 + 3, y2 - 3, 4):
        for wx in range(x + 2, x + w - 2, 3):
            lit = ((wx * 13 + wy * 7 + frame * 3 + bi) % 11) > 3
            d.point((wx, wy), fill=win_on if lit else win_off)
    # neon strip
    if bi % 2 == 0:
        col = (41, 173, 255, 220) if bi % 4 == 0 else (255, 0, 77, 200)
        d.rectangle([x + 1, y1 + 6, x + w - 1, y1 + 7], fill=col)


def make_rate():
    """Ops command deck — dense CRT radar + rack lights. No chibi."""
    W, H, N = 220, 120, 20
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (8, 10, 18, 255))
        d = ImageDraw.Draw(im)
        # metal panel background
        for y in range(H):
            c = 10 + (y % 6)
            d.line([(0, y), (W, y)], fill=(c, c + 2, c + 8, 255))
        # left rack
        d.rectangle([0, 0, 48, H], fill=(14, 16, 28, 255), outline=(40, 44, 70, 255))
        for k in range(9):
            y = 8 + k * 12
            lit = (i + k) % 5 != 0
            d.rectangle([8, y, 40, y + 7], fill=(20, 24, 40, 255), outline=(60, 70, 100, 255))
            d.rectangle([10, y + 2, 16, y + 5], fill=(0, 228, 54, 255) if lit else (40, 60, 40, 255))
            d.rectangle([20, y + 2, 26, y + 5], fill=(41, 173, 255, 255) if (i + k) % 3 == 0 else (30, 40, 60, 255))
            d.rectangle([30, y + 2, 36, y + 5], fill=(255, 163, 0, 255) if (i + k) % 7 == 0 else (50, 40, 20, 255))
        # main CRT bezel
        d.rectangle([56, 8, 168, 108], fill=(6, 8, 12, 255), outline=(180, 190, 210, 255))
        d.rectangle([60, 12, 164, 104], fill=(4, 18, 14, 255), outline=(0, 0, 0, 255))
        # phosphor grid
        for x in range(64, 161, 6):
            d.line([(x, 14), (x, 102)], fill=(0, 60, 40, 40))
        for y in range(16, 101, 6):
            d.line([(62, y), (162, y)], fill=(0, 60, 40, 40))
        cx, cy, maxr = 112, 58, 40
        # concentric rings
        for r in (12, 22, 32, 40):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 180, 90, 90))
        d.line([(cx - maxr, cy), (cx + maxr, cy)], fill=(0, 140, 70, 70))
        d.line([(cx, cy - maxr), (cx, cy + maxr)], fill=(0, 140, 70, 70))
        # sweep
        ang0 = (i / N) * 360.0
        for a in range(0, 70, 2):
            ang = math.radians(ang0 - a)
            fade = 1 - a / 70
            col = (120, 255, 160, int(210 * fade)) if a < 25 else (255, 220, 60, int(120 * fade))
            x2 = cx + int(math.cos(ang) * maxr)
            y2 = cy + int(math.sin(ang) * maxr)
            d.line([(cx, cy), (x2, y2)], fill=col, width=2)
        # contacts / blips
        contacts = [(18, 40), (28, 210), (34, 120), (22, 300), (30, 70)]
        for j, (rr, base) in enumerate(contacts):
            ang = math.radians(base + i * (4 + j))
            bx = cx + int(math.cos(ang) * rr)
            by = cy + int(math.sin(ang) * rr)
            pulse = 1 + ((i + j) % 3 == 0)
            d.rectangle([bx - pulse, by - pulse, bx + pulse, by + pulse], fill=(255, 60, 90, 255))
        # side telemetry columns
        d.rectangle([176, 8, 214, 108], fill=(12, 14, 24, 255), outline=(70, 80, 120, 255))
        for r in range(14):
            y = 14 + r * 6
            w = 8 + int(20 + 12 * math.sin((i + r) / 2.3))
            d.rectangle([180, y, 180 + w, y + 3], fill=(41, 173, 255, 200) if r % 2 == 0 else (0, 228, 54, 180))
        # tick labels fake
        for t, label_y in enumerate((16, 40, 64, 88)):
            d.rectangle([182, label_y, 210, label_y + 2], fill=(90, 100, 130, 255))
        im = vignette(im, 0.35)
        im = scanlines(im, 35)
        im = noise(im, 8, seed=i)
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "rate.gif"), 65)


def make_nodes():
    """Rain-soaked neo-Tokyo skyline — dense, adult cyber look."""
    W, H, N = 180, 120, 18
    frames = []
    sky_top = (8, 6, 20)
    sky_bot = (24, 12, 40)
    buildings = []
    rng = random.Random(42)
    x = 0
    bi = 0
    while x < W:
        w = rng.randint(10, 22)
        h = rng.randint(36, 95)
        buildings.append((x, H - 12 - h, w, h, bi))
        x += w - rng.randint(0, 2)
        bi += 1
    for i in range(N):
        im = Image.new("RGBA", (W, H), (10, 8, 22, 255))
        d = ImageDraw.Draw(im)
        # sky gradient
        for y in range(H - 12):
            t = y / (H - 12)
            col = blend(sky_top + (255,), sky_bot + (255,), t)
            d.line([(0, y), (W, y)], fill=col)
        # distant haze city silhouette
        for x in range(0, W, 3):
            hh = 20 + int(10 * math.sin(x / 17 + i / 8))
            d.rectangle([x, H - 20 - hh, x + 2, H - 18], fill=(30, 20, 50, 255))
        # moon
        d.ellipse([140, 10, 168, 38], fill=(220, 225, 245, 255), outline=(120, 150, 220, 255))
        d.ellipse([148, 16, 160, 28], fill=(24, 12, 40, 255))
        # stars
        rngs = random.Random(7)
        for _ in range(40):
            sx = rngs.randint(0, W - 1)
            sy = rngs.randint(0, 50)
            if (sx + sy + i) % 5:
                d.point((sx, sy), fill=(200, 210, 255, 230))
        # buildings
        for x, y1, w, h, bi in buildings:
            body = (18 + (bi % 4) * 2, 18, 34 + (bi % 3) * 3, 255)
            edge = (50, 60, 100, 255)
            win_on = (255, 220, 120, 255) if bi % 3 else (120, 200, 255, 255)
            win_off = (24, 28, 40, 255)
            draw_building(d, x, y1, H - 12, w, i, bi, (body, edge, win_on, win_off))
            # billboard
            if bi % 5 == 0 and w > 12:
                d.rectangle([x + 2, y1 + 10, x + w - 2, y1 + 18], fill=(255, 0, 77, 220) if (i + bi) % 2 == 0 else (41, 173, 255, 220))
        # elevated highway
        d.rectangle([0, H - 34, W, H - 28], fill=(40, 40, 60, 255), outline=(80, 90, 130, 255))
        for lx in range(-20, W, 16):
            xx = lx + (i * 2) % 16
            d.rectangle([xx, H - 33, xx + 6, H - 31], fill=(255, 200, 80, 200))
        # cars
        for c in range(4):
            cx = (i * (3 + c) + c * 40) % (W + 30) - 15
            cy = H - 32
            d.rectangle([cx, cy, cx + 10, cy + 3], fill=(20, 20, 28, 255))
            d.point((cx + 1, cy + 1), fill=(255, 80, 80, 255))
            d.point((cx + 8, cy + 1), fill=(255, 220, 100, 255))
        # rain
        rngr = random.Random(i + 99)
        for _ in range(70):
            rx = rngr.randint(0, W - 1)
            ry = (rngr.randint(0, H - 1) + i * 5) % H
            d.line([(rx, ry), (rx - 1, ry + 5)], fill=(160, 180, 220, 90))
        # ground wet reflection strip
        d.rectangle([0, H - 12, W, H], fill=(12, 10, 22, 255))
        for x in range(0, W, 4):
            d.point((x, H - 6 + (i + x) % 3), fill=(41, 173, 255, 100))
        # network arcs in sky
        for a in range(3):
            y = 18 + a * 8
            d.arc([10, y, W - 10, y + 50], 200, 340, fill=(41, 173, 255, 60 + a * 15))
        # packets
        for p in range(6):
            t = ((i + p * 3) % N) / N
            x = int(8 + t * (W - 16))
            y = int(16 + 10 * math.sin((t * 6 + p) * math.pi) + p * 4)
            col = (255, 236, 39, 255) if p % 2 == 0 else (255, 100, 160, 255)
            d.rectangle([x, y, x + 3, y + 2], fill=col)
        im = vignette(im, 0.4)
        im = scanlines(im, 25)
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "nodes.gif"), 70)


def make_ok():
    """Clean server farm / green grid corridor — mature tech, not cute."""
    W, H, N = 180, 120, 16
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (6, 16, 12, 255))
        d = ImageDraw.Draw(im)
        # perspective floor grid
        vanishing_y = 40
        for g in range(1, 14):
            y = vanishing_y + int((H - vanishing_y) * (g / 14) ** 1.4)
            d.line([(0, y), (W, y)], fill=(0, 90, 50, 70 + g * 4))
        for g in range(-10, 11):
            x1 = W // 2 + g * 6
            d.line([(x1, vanishing_y), (W // 2 + g * 28, H)], fill=(0, 100, 55, 50))
        # server racks left/right
        for side, x0 in (("L", 8), ("R", 132)):
            for r in range(5):
                y = 18 + r * 18
                d.rectangle([x0, y, x0 + 40, y + 15], fill=(12, 20, 18, 255), outline=(0, 120, 70, 255))
                for led in range(8):
                    lx = x0 + 4 + led * 4
                    on = ((i + led + r) % 4) != 0
                    d.rectangle([lx, y + 4, lx + 2, y + 10], fill=(0, 228, 54, 255) if on else (20, 50, 30, 255))
                # activity bar
                bw = 6 + int(20 + 10 * math.sin((i + r) / 2))
                d.rectangle([x0 + 4, y + 12, x0 + 4 + bw, y + 13], fill=(41, 173, 255, 200))
        # center hologram pillar
        cx = W // 2
        for layer in range(6):
            rr = 6 + layer * 4 + (i % 4)
            alpha = 110 - layer * 12
            d.ellipse([cx - rr, 28 - layer, cx + rr, 52 + layer], outline=(0, 228, 54, alpha))
        d.rectangle([cx - 3, 30, cx + 3, 90], fill=(40, 255, 140, 60), outline=(0, 228, 54, 180))
        # rising particles
        for p in range(18):
            px = 30 + (p * 7 + i) % (W - 60)
            py = (H - 10 - (i * 3 + p * 9) % (H - 20))
            d.point((px, py), fill=(180, 255, 200, 220))
        # top status ribbon
        d.rectangle([50, 6, 130, 14], fill=(0, 40, 24, 255), outline=(0, 228, 54, 200))
        for s in range(8):
            d.rectangle([54 + s * 9, 8, 60 + s * 9, 12], fill=(0, 228, 54, 255) if (i + s) % 3 else (0, 80, 40, 255))
        # subtle floor reflection glow
        d.ellipse([cx - 40, 92, cx + 40, 112], fill=(0, 228, 54, 25))
        im = vignette(im, 0.38)
        im = scanlines(im, 28)
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "ok.gif"), 75)


def make_err():
    """Industrial alert bay — siren, cracked CRT, glitch — serious, not toy-like."""
    W, H, N = 180, 120, 14
    frames = []
    for i in range(N):
        flash = i % 2 == 0
        im = Image.new("RGBA", (W, H), (22, 6, 10, 255))
        d = ImageDraw.Draw(im)
        # dark metal
        for y in range(H):
            c = 18 + (y % 5)
            d.line([(0, y), (W, y)], fill=(c + 8, c // 2, c // 2 + 4, 255))
        # hazard stripe top/bottom
        for x in range(-20, W, 14):
            off = (i * 3) % 14
            d.rectangle([x + off, 0, x + off + 7, 8], fill=(255, 210, 40, 255))
            d.rectangle([x + off + 7, 0, x + off + 14, 8], fill=(20, 10, 12, 255))
            d.rectangle([x - off, H - 8, x - off + 7, H], fill=(255, 210, 40, 255))
            d.rectangle([x - off + 7, H - 8, x - off + 14, H], fill=(20, 10, 12, 255))
        # siren dome
        d.ellipse([18, 18, 54, 50], fill=(255, 0, 60, 255) if flash else (120, 0, 30, 255), outline=(255, 120, 140, 255))
        d.rectangle([28, 46, 44, 58], fill=(40, 10, 16, 255), outline=(180, 40, 60, 255))
        if flash:
            d.polygon([(36, 34), (-5, 90), (20, 90)], fill=(255, 0, 60, 40))
            d.polygon([(36, 34), (55, 90), (90, 90)], fill=(255, 0, 60, 35))
        # main broken CRT
        d.rectangle([70, 20, 164, 96], fill=(8, 6, 10, 255), outline=(200, 60, 80, 255))
        d.rectangle([74, 24, 160, 92], fill=(16, 4, 8, 255))
        # glitch bars
        rng = random.Random(i * 17 + 3)
        for _ in range(16):
            y = rng.randint(26, 88)
            x = rng.randint(76, 120)
            w = rng.randint(10, 50)
            col = (255, 0, 77, 160) if rng.random() > 0.4 else (255, 236, 39, 140)
            d.rectangle([x, y, x + w, y + rng.randint(1, 3)], fill=col)
        # static pixels
        for _ in range(120):
            x = rng.randint(76, 158)
            y = rng.randint(26, 90)
            d.point((x, y), fill=(255, 80, 100, 180) if rng.random() > 0.5 else (220, 220, 220, 100))
        # crack lines
        d.line([(100, 30), (130, 70), (150, 40)], fill=(255, 200, 200, 180), width=1)
        d.line([(90, 80), (120, 50)], fill=(255, 150, 160, 150), width=1)
        # alert text blocks (abstract, not fonts)
        for row in range(3):
            for col in range(6):
                if (row + col + i) % 3 != 0:
                    d.rectangle(
                        [78 + col * 12, 30 + row * 10, 86 + col * 12, 36 + row * 10],
                        fill=(255, 236, 39, 255) if flash else (255, 0, 77, 255),
                    )
        # right status column
        d.rectangle([168, 20, 176, 96], fill=(30, 8, 12, 255), outline=(120, 30, 40, 255))
        for k in range(10):
            y = 24 + k * 7
            d.rectangle([170, y, 174, y + 4], fill=(255, 0, 77, 255) if (i + k) % 2 == 0 else (60, 20, 24, 255))
        # shake offset effect via slight content already random
        im = vignette(im, 0.42)
        im = scanlines(im, 40)
        if flash:
            # red overlay pulse
            overlay = Image.new("RGBA", (W, H), (255, 0, 40, 28))
            im = Image.alpha_composite(im, overlay)
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "err.gif"), 85)


def make_detail():
    """Night ops loft — panoramic city window, dual CRTs, no childish props."""
    W, H, N = 280, 190, 18
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (10, 10, 18, 255))
        d = ImageDraw.Draw(im)
        # room walls
        d.rectangle([0, 0, W, H], fill=(16, 14, 26, 255))
        # panoramic window frame
        d.rectangle([16, 12, 264, 108], fill=(8, 12, 28, 255), outline=(90, 110, 160, 255))
        d.rectangle([20, 16, 260, 104], fill=(12, 16, 36, 255))
        # sky
        for y in range(16, 104):
            t = (y - 16) / 88
            col = blend((10, 10, 28, 255), (40, 18, 50, 255), t)
            d.line([(22, y), (258, y)], fill=col)
        # moon
        d.ellipse([220, 24, 248, 52], fill=(225, 230, 250, 255), outline=(120, 150, 220, 255))
        # far mountains
        d.polygon([(22, 90), (60, 55), (100, 88), (140, 50), (180, 86), (220, 58), (258, 92), (258, 104), (22, 104)], fill=(20, 18, 40, 255))
        # dense city
        rng = random.Random(5)
        x = 24
        bi = 0
        while x < 250:
            w = rng.randint(8, 18)
            h = rng.randint(20, 55)
            y1 = 104 - h
            d.rectangle([x, y1, x + w, 104], fill=(22, 24, 42, 255), outline=(50, 70, 120, 200))
            for wy in range(y1 + 3, 102, 4):
                for wx in range(x + 2, x + w - 1, 3):
                    if ((wx + wy + i + bi) % 5) > 1:
                        d.point((wx, wy), fill=(255, 210, 100, 255) if (wx + bi) % 2 else (100, 180, 255, 255))
            if bi % 4 == 0:
                d.rectangle([x + 1, y1 + 5, x + w - 1, y1 + 8], fill=(255, 0, 90, 200) if (i + bi) % 2 == 0 else (0, 220, 255, 200))
            x += w + rng.randint(-1, 2)
            bi += 1
        # rain on window
        rngr = random.Random(i + 3)
        for _ in range(50):
            rx = rngr.randint(22, 258)
            ry = (rngr.randint(16, 100) + i * 4) % 100
            if 16 <= ry <= 100:
                d.line([(rx, ry), (rx - 1, ry + 4)], fill=(170, 190, 230, 70))
        # desk surface
        d.rectangle([0, 128, W, H], fill=(28, 22, 40, 255))
        d.rectangle([0, 126, W, 128], fill=(200, 180, 80, 220))
        # dual monitors with real-looking UI chrome
        for mi, mx in enumerate((48, 150)):
            d.rectangle([mx, 78, mx + 84, 128], fill=(6, 8, 14, 255), outline=(210, 200, 120, 255))
            d.rectangle([mx + 3, 81, mx + 81, 118], fill=(8, 14, 20, 255))
            # UI header
            d.rectangle([mx + 3, 81, mx + 81, 90], fill=(20, 28, 48, 255))
            d.rectangle([mx + 6, 83, mx + 20, 87], fill=(41, 173, 255, 255))
            # code/log rows
            for r in range(6):
                y = 94 + r * 4
                w = 20 + ((i * 5 + r * 9 + mi * 7) % 45)
                col = (0, 220, 120, 210) if (r + i + mi) % 3 else (80, 160, 255, 180)
                d.rectangle([mx + 8, y, mx + 8 + w, y + 2], fill=col)
            # stand
            d.rectangle([mx + 36, 118, mx + 48, 128], fill=(40, 40, 55, 255))
        # keyboard
        d.rectangle([96, 142, 184, 156], fill=(18, 18, 26, 255), outline=(120, 120, 150, 255))
        for k in range(14):
            d.rectangle([100 + k * 6, 146, 104 + k * 6, 151], fill=(55, 55, 75, 255))
        # mug (simple adult desk object)
        d.rectangle([200, 140, 214, 156], fill=(50, 40, 55, 255), outline=(180, 120, 140, 255))
        d.arc([212, 144, 220, 154], 270, 90, fill=(180, 120, 140, 255))
        # cable mess subtle
        d.line([(84, 128), (70, 150), (48, 160)], fill=(40, 40, 60, 255))
        d.line([(196, 128), (220, 150), (250, 160)], fill=(40, 40, 60, 255))
        # floating status chips on desk front
        for fi, x in enumerate((40, 100, 160, 220)):
            on = (i + fi) % 4 != 0
            d.rectangle([x, 168, x + 40, 178], fill=(0, 180, 90, 255) if on else (50, 50, 70, 255), outline=(0, 0, 0, 255))
        # ambient lamp glow left
        d.ellipse([-10, 100, 40, 150], fill=(255, 180, 80, 25))
        im = vignette(im, 0.4)
        im = scanlines(im, 22)
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "detail.gif"), 75)


if __name__ == "__main__":
    make_rate()
    make_nodes()
    make_ok()
    make_err()
    make_detail()
    print("mature cyber defaults ready")
