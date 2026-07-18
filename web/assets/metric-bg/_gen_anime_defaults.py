"""Original anime-aesthetic pixel loops for default scene backgrounds.
No copyrighted anime frames — all procedurally drawn.
"""
from PIL import Image, ImageDraw
import os
import math
import random

OUT = os.path.dirname(os.path.abspath(__file__))


def quant(im):
    return im.convert("P", palette=Image.ADAPTIVE, colors=128)


def save_gif(frames, path, duration=90):
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


def rect(d, box, fill=None, outline=None):
    d.rectangle(box, fill=fill, outline=outline)


def skin():
    return (255, 214, 186, 255)


def hair_dark():
    return (42, 28, 68, 255)


def hair_pink():
    return (255, 119, 168, 255)


def hair_blue():
    return (90, 170, 255, 255)


def draw_girl_bust(d, x, y, frame, hair="pink", eyes_open=True):
    """Very small original chibi bust (not a known character)."""
    # head
    d.ellipse([x, y, x + 22, y + 24], fill=skin(), outline=(40, 30, 40, 255))
    # hair back
    hc = hair_pink() if hair == "pink" else hair_blue() if hair == "blue" else hair_dark()
    d.ellipse([x - 2, y - 4, x + 24, y + 16], fill=hc)
    # side hair
    d.polygon([(x - 1, y + 8), (x - 6, y + 26), (x + 4, y + 18)], fill=hc)
    d.polygon([(x + 23, y + 8), (x + 28, y + 26), (x + 18, y + 18)], fill=hc)
    # bangs
    for i in range(4):
        bx = x + 3 + i * 4
        d.polygon([(bx, y + 2), (bx + 3, y + 2), (bx + 1, y + 10)], fill=hc)
    # eyes
    if eyes_open:
        blink = frame % 10 == 7
        if not blink:
            rect(d, [x + 6, y + 11, x + 9, y + 15], fill=(30, 30, 50, 255))
            rect(d, [x + 13, y + 11, x + 16, y + 15], fill=(30, 30, 50, 255))
            d.point((x + 8, y + 12), fill=(255, 255, 255, 255))
            d.point((x + 15, y + 12), fill=(255, 255, 255, 255))
        else:
            d.line([(x + 6, y + 13), (x + 9, y + 13)], fill=(30, 30, 50, 255))
            d.line([(x + 13, y + 13), (x + 16, y + 13)], fill=(30, 30, 50, 255))
    # blush
    rect(d, [x + 4, y + 16, x + 6, y + 17], fill=(255, 150, 160, 180))
    rect(d, [x + 16, y + 16, x + 18, y + 17], fill=(255, 150, 160, 180))
    # body / hoodie
    rect(d, [x + 2, y + 22, x + 20, y + 36], fill=(255, 236, 39, 255), outline=(26, 28, 44, 255))
    # collar
    d.line([(x + 6, y + 24), (x + 16, y + 24)], fill=(26, 28, 44, 255))


def make_rate():
    """Anime operator girl + radar HUD."""
    W, H, N = 180, 100, 16
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (18, 16, 32, 255))
        d = ImageDraw.Draw(im)
        # room gradient-ish bands
        for y in range(H):
            shade = 18 + y // 8
            d.line([(0, y), (W, y)], fill=(shade, shade - 2, shade + 14, 255))
        # window
        rect(d, [8, 8, 100, 70], fill=(20, 40, 60, 255), outline=(255, 236, 39, 255))
        # city through window
        for bi, (bx, bh) in enumerate([(14, 28), (28, 40), (42, 24), (56, 44), (72, 30), (86, 38)]):
            rect(d, [bx, 70 - bh, bx + 10, 70], fill=(26, 28, 44, 255), outline=(41, 173, 255, 100))
            if (i + bi) % 3 == 0:
                d.point((bx + 4, 70 - bh + 8), fill=(255, 236, 39, 255))
        # radar circle overlay on window
        cx, cy, maxr = 54, 40, 22
        for r in (8, 14, 20):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 228, 54, 100))
        ang0 = (i / N) * 360
        for a in range(0, 40, 3):
            ang = math.radians(ang0 + a)
            fade = 1 - a / 40
            x2 = cx + int(math.cos(ang) * maxr)
            y2 = cy + int(math.sin(ang) * maxr)
            d.line([(cx, cy), (x2, y2)], fill=(255, 236, 39, int(180 * fade)), width=2)
        # desk
        rect(d, [0, 74, W, H], fill=(40, 32, 56, 255))
        rect(d, [0, 72, W, 74], fill=(255, 236, 39, 220))
        # girl
        draw_girl_bust(d, 118, 34, i, hair="pink")
        # floating HUD chips
        for hi, labx in enumerate((12, 40, 68)):
            on = (i + hi) % 4 != 0
            rect(
                d,
                [labx, 80, labx + 22, 90],
                fill=(0, 228, 54, 255) if on else (60, 60, 90, 255),
                outline=(0, 0, 0, 255),
            )
        # scanlines
        for y in range(0, H, 3):
            d.line([(0, y), (W, y)], fill=(0, 0, 0, 28))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "rate.gif"), 80)


def make_nodes():
    """Anime cyber night city with flying packets + tiny rider silhouette."""
    W, H, N = 140, 100, 14
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (12, 14, 30, 255))
        d = ImageDraw.Draw(im)
        # moon
        d.ellipse([108, 8, 128, 28], fill=(230, 235, 255, 255), outline=(41, 173, 255, 255))
        # stars
        rng = random.Random(9)
        for _ in range(28):
            x = rng.randint(0, W - 1)
            y = rng.randint(0, 45)
            if (x + y + i) % 4:
                d.point((x, y), fill=(200, 210, 255, 220))
        # buildings
        buildings = [
            (0, 48, 16, 100),
            (16, 36, 34, 100),
            (34, 52, 48, 100),
            (48, 28, 68, 100),
            (68, 44, 84, 100),
            (84, 32, 104, 100),
            (104, 50, 122, 100),
            (122, 40, 140, 100),
        ]
        for bi, (x1, y1, x2, y2) in enumerate(buildings):
            rect(d, [x1, y1, x2, y2], fill=(24, 26, 44, 255), outline=(41, 173, 255, 120))
            for wy in range(y1 + 4, y2 - 4, 5):
                for wx in range(x1 + 3, x2 - 3, 4):
                    on = ((wx + wy + i + bi) % 5) != 0
                    d.point((wx, wy), fill=(255, 236, 39, 255) if on else (50, 55, 80, 255))
        # neon signs
        rect(d, [52, 34, 64, 40], fill=(255, 0, 77, 255))
        rect(d, [88, 38, 100, 44], fill=(0, 228, 54, 255))
        # network arcs
        for a in range(3):
            y = 16 + a * 7
            d.arc([8, y, 132, y + 36], 200, 340, fill=(41, 173, 255, 110))
        # packets
        for p in range(5):
            t = ((i + p * 2) % N) / N
            x = int(6 + t * (W - 12))
            y = int(14 + 8 * math.sin((t * 5 + p) * math.pi) + p * 5)
            rect(
                d,
                [x, y, x + 3, y + 2],
                fill=(255, 236, 39, 255) if p % 2 == 0 else (255, 119, 168, 255),
            )
        # tiny anime scooter silhouette bottom
        sx = (i * 5) % (W + 20) - 10
        rect(d, [sx, 88, sx + 14, 94], fill=(40, 40, 55, 255))
        d.ellipse([sx + 1, 92, sx + 5, 96], fill=(20, 20, 30, 255))
        d.ellipse([sx + 9, 92, sx + 13, 96], fill=(20, 20, 30, 255))
        # rider
        d.ellipse([sx + 5, 80, sx + 11, 86], fill=(255, 214, 186, 255))
        rect(d, [sx + 6, 86, sx + 10, 91], fill=(90, 170, 255, 255))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "nodes.gif"), 90)


def make_ok():
    """Anime healing shrine / sparkle field + smiling girl."""
    W, H, N = 140, 100, 14
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (18, 36, 28, 255))
        d = ImageDraw.Draw(im)
        # sky wash
        for y in range(0, 50):
            d.line([(0, y), (W, y)], fill=(30 + y // 2, 70 + y // 3, 50 + y // 4, 255))
        # sun
        d.ellipse([100, 8, 124, 32], fill=(255, 236, 39, 255), outline=(255, 163, 0, 255))
        # hills
        d.ellipse([-20, 48, 90, 120], fill=(20, 90, 50, 255))
        d.ellipse([60, 54, 160, 120], fill=(16, 80, 44, 255))
        # shrine gate (torii-ish simple)
        rect(d, [30, 40, 34, 78], fill=(255, 0, 77, 255))
        rect(d, [70, 40, 74, 78], fill=(255, 0, 77, 255))
        rect(d, [24, 36, 80, 42], fill=(255, 0, 77, 255))
        rect(d, [26, 44, 78, 48], fill=(255, 80, 100, 255))
        # crystal
        d.polygon([(52, 50), (44, 70), (60, 70)], fill=(0, 228, 54, 255), outline=(255, 255, 255, 200))
        # girl right
        draw_girl_bust(d, 96, 42, i, hair="blue")
        # sparkles
        for s in range(10):
            ang = (i * 18 + s * 36) % 360
            rad = 12 + (s % 4) * 5
            sx = 52 + int(math.cos(math.radians(ang)) * rad)
            sy = 58 + int(math.sin(math.radians(ang)) * rad / 1.4)
            if 0 <= sx < W and 0 <= sy < H:
                d.point((sx, sy), fill=(255, 236, 39, 255))
        # rising equalizer
        for k in range(9):
            h = 5 + int(8 + 6 * math.sin((i + k) / 2.0))
            x = 12 + k * 8
            rect(d, [x, 92 - h, x + 5, 94], fill=(0, 228, 54, 255), outline=(0, 60, 20, 255))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "ok.gif"), 85)


def make_err():
    """Anime alert room — girl with headset + siren."""
    W, H, N = 140, 100, 12
    frames = []
    for i in range(N):
        flash = i % 2 == 0
        im = Image.new("RGBA", (W, H), (36, 12, 20, 255))
        d = ImageDraw.Draw(im)
        border = (255, 0, 77, 255) if flash else (120, 20, 40, 255)
        rect(d, [0, 0, W - 1, H - 1], outline=border)
        # hazard stripe
        for x in range(0, W, 12):
            off = (i * 3) % 12
            rect(d, [x - off, 0, x - off + 6, 8], fill=(255, 236, 39, 255))
            rect(d, [x - off + 6, 0, x - off + 12, 8], fill=(26, 28, 44, 255))
        # siren
        d.ellipse([18, 18, 42, 40], fill=(255, 0, 77, 255) if flash else (160, 0, 40, 255))
        rect(d, [24, 36, 36, 48], fill=(80, 0, 20, 255), outline=(255, 0, 77, 255))
        if flash:
            d.polygon([(30, 28), (0, 70), (18, 70)], fill=(255, 0, 77, 45))
            d.polygon([(30, 28), (50, 70), (70, 70)], fill=(255, 0, 77, 45))
        # terminal
        rect(d, [12, 56, 78, 92], fill=(10, 10, 14, 255), outline=(255, 0, 77, 220))
        for y in range(62, 88, 4):
            w = 20 + ((i * 7 + y) % 40)
            d.line([(18, y), (18 + w, y)], fill=(255, 0, 77, 180))
        # girl with headset
        draw_girl_bust(d, 92, 30, i, hair="dark")
        # headset
        d.arc([90, 34, 116, 54], 200, 340, fill=(200, 200, 220, 255))
        rect(d, [90, 42, 94, 50], fill=(80, 80, 100, 255))
        rect(d, [112, 42, 116, 50], fill=(80, 80, 100, 255))
        # ALERT blocks
        for bx in range(4):
            rect(
                d,
                [20 + bx * 12, 70, 28 + bx * 12, 78],
                fill=(255, 236, 39, 255) if flash else (255, 0, 77, 255),
            )
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "err.gif"), 100)


def make_detail():
    """Anime night lounge — dual monitors, city window, desk cat."""
    W, H, N = 240, 170, 16
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (14, 12, 24, 255))
        d = ImageDraw.Draw(im)
        # big window
        rect(d, [12, 10, 228, 95], fill=(16, 22, 42, 255), outline=(41, 173, 255, 200))
        # city
        for bi, (x, h) in enumerate(
            [
                (20, 42),
                (38, 58),
                (56, 36),
                (74, 64),
                (94, 48),
                (114, 70),
                (136, 40),
                (156, 60),
                (176, 46),
                (196, 66),
            ]
        ):
            rect(d, [x, 95 - h, x + 14, 95], fill=(26, 28, 44, 255), outline=(41, 173, 255, 90))
            for wy in range(95 - h + 4, 93, 6):
                if ((wy // 6) + i + bi) % 3 == 0:
                    d.point((x + 4, wy), fill=(255, 236, 39, 255))
                    d.point((x + 9, wy), fill=(255, 119, 168, 255))
        # moon + stars
        d.ellipse([190, 18, 214, 42], fill=(230, 235, 255, 255), outline=(41, 173, 255, 255))
        rng = random.Random(2)
        for _ in range(35):
            x = rng.randint(16, 220)
            y = rng.randint(14, 70)
            if (x + y + i) % 4:
                d.point((x, y), fill=(210, 220, 255, 220))
        # desk
        rect(d, [0, 118, W, H], fill=(34, 26, 48, 255))
        rect(d, [0, 116, W, 118], fill=(255, 236, 39, 220))
        # dual monitors
        for mx in (40, 130):
            rect(d, [mx, 74, mx + 70, 118], fill=(8, 10, 16, 255), outline=(255, 236, 39, 255))
            for c in range(8):
                for r in range(7):
                    y = 80 + r * 5 + (i + c) % 3
                    if y < 114:
                        col = (0, 228, 54, 210) if (r + i + c) % 3 else (41, 173, 255, 170)
                        rect(d, [mx + 8 + c * 7, y, mx + 12 + c * 7, y + 2], fill=col)
        # keyboard
        rect(d, [78, 132, 162, 146], fill=(22, 22, 30, 255), outline=(180, 180, 200, 255))
        for k in range(11):
            rect(d, [84 + k * 7, 136, 88 + k * 7, 140], fill=(70, 70, 90, 255))
        # girl left side of desk
        draw_girl_bust(d, 12, 92, i, hair="pink")
        # cat right
        d.ellipse([184, 126, 218, 150], fill=(45, 42, 55, 255), outline=(255, 119, 168, 220))
        d.ellipse([204, 114, 228, 136], fill=(45, 42, 55, 255), outline=(255, 119, 168, 220))
        d.polygon([(206, 118), (210, 104), (214, 118)], fill=(45, 42, 55, 255))
        d.polygon([(216, 118), (222, 104), (226, 118)], fill=(45, 42, 55, 255))
        if i % 8 != 4:
            rect(d, [210, 122, 212, 124], fill=(255, 236, 39, 255))
            rect(d, [218, 122, 220, 124], fill=(255, 236, 39, 255))
        # floating chips
        for fi, x in enumerate((50, 100, 150)):
            on = (i + fi) % 4 != 0
            rect(
                d,
                [x, 154, x + 36, 162],
                fill=(0, 228, 54, 255) if on else (70, 70, 90, 255),
                outline=(0, 0, 0, 255),
            )
        for y in range(0, H, 3):
            d.line([(0, y), (W, y)], fill=(0, 0, 0, 26))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "detail.gif"), 90)


if __name__ == "__main__":
    make_rate()
    make_nodes()
    make_ok()
    make_err()
    make_detail()
    print("anime defaults ready")
