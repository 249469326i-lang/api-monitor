"""Richer pixel-loop GIFs for metric cards + detail empty panel."""
from PIL import Image, ImageDraw
import os
import math
import random

OUT = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)


def quant(im):
    return im.convert("P", palette=Image.ADAPTIVE, colors=96)


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
    print(
        "wrote",
        os.path.basename(path),
        frames[0].size,
        len(frames),
        "f",
        os.path.getsize(path),
        "B",
    )


def rect(d, box, fill=None, outline=None):
    d.rectangle(box, fill=fill, outline=outline)


def make_rate():
    W, H, N = 160, 96, 18
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (20, 18, 34, 255))
        d = ImageDraw.Draw(im)
        rect(d, [0, 0, W - 1, H - 1], outline=(255, 236, 39, 255))
        rect(d, [3, 3, W - 4, H - 4], outline=(26, 28, 44, 255))
        rect(d, [8, 8, 118, H - 9], fill=(12, 40, 28, 255), outline=(0, 0, 0, 255))
        for y in range(10, H - 10, 2):
            d.line([(10, y), (116, y)], fill=(0, 0, 0, 40))
        cx, cy, maxr = 63, 50, 34
        for r in (10, 18, 26, 34):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 228, 54, 90))
        d.line([(cx - maxr, cy), (cx + maxr, cy)], fill=(0, 228, 54, 60))
        d.line([(cx, cy - maxr), (cx, cy + maxr)], fill=(0, 228, 54, 60))
        ang0 = (i / N) * 360
        for a in range(0, 55, 2):
            ang = math.radians(ang0 + a)
            fade = 1 - a / 55
            col = (
                (255, 236, 39, int(200 * fade))
                if a < 28
                else (255, 163, 0, int(120 * fade))
            )
            x2 = cx + int(math.cos(ang) * maxr)
            y2 = cy + int(math.sin(ang) * maxr)
            d.line([(cx, cy), (x2, y2)], fill=col, width=2)
        for j, (rr, aa) in enumerate([(16, 40), (24, 200), (30, 120)]):
            ang = math.radians(aa + i * 12 + j * 20)
            bx = cx + int(math.cos(ang) * rr)
            by = cy + int(math.sin(ang) * rr)
            if (i + j) % 3 != 0:
                rect(d, [bx - 1, by - 1, bx + 1, by + 1], fill=(255, 0, 77, 255))
        rect(d, [124, 10, 152, H - 11], fill=(40, 30, 60, 255), outline=(255, 236, 39, 255))
        for k in range(5):
            y = 18 + k * 12
            lit = (i + k) % 4 == 0
            rect(
                d,
                [130, y, 146, y + 6],
                fill=(255, 236, 39, 255) if lit else (80, 70, 40, 255),
                outline=(0, 0, 0, 255),
            )
        glow = 8 + (i % 4)
        d.ellipse(
            [cx - glow, cy - glow, cx + glow, cy + glow],
            outline=(255, 236, 39, 70),
        )
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "rate.gif"), 70)


def make_nodes():
    W, H, N = 120, 96, 14
    frames = []
    buildings = [
        (4, 50, 14, 90),
        (18, 40, 28, 90),
        (32, 55, 42, 90),
        (46, 30, 58, 90),
        (62, 48, 74, 90),
        (78, 36, 90, 90),
        (94, 58, 110, 90),
    ]
    for i in range(N):
        im = Image.new("RGBA", (W, H), (10, 14, 28, 255))
        d = ImageDraw.Draw(im)
        rng = random.Random(3)
        for _ in range(25):
            x = rng.randint(0, W - 1)
            y = rng.randint(0, 40)
            if (i + x + y) % 5 != 0:
                d.point((x, y), fill=(180, 200, 255, 180))
        d.ellipse([96, 8, 112, 24], fill=(220, 230, 255, 255), outline=(41, 173, 255, 255))
        for bi, (x1, y1, x2, y2) in enumerate(buildings):
            rect(d, [x1, y1, x2, y2], fill=(26, 28, 44, 255), outline=(41, 173, 255, 120))
            for wy in range(y1 + 3, y2 - 3, 5):
                for wx in range(x1 + 2, x2 - 2, 4):
                    on = ((wx + wy + i + bi) % 4) != 0
                    d.point(
                        (wx, wy),
                        fill=(255, 236, 39, 255) if on else (40, 50, 70, 255),
                    )
        for a in range(3):
            y = 20 + a * 8 + (i % 3)
            d.arc([10, y, 110, y + 40], 200, 340, fill=(41, 173, 255, 100))
        for p in range(4):
            t = ((i + p * 3) % N) / N
            x = int(8 + t * (W - 16))
            y = int(18 + 10 * math.sin((t * 6 + p) * math.pi) + p * 6)
            rect(
                d,
                [x, y, x + 3, y + 2],
                fill=(255, 236, 39, 255) if p % 2 == 0 else (41, 173, 255, 255),
            )
        d.line([(0, H - 6), (W, H - 6)], fill=(41, 173, 255, 160))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "nodes.gif"), 90)


def make_ok():
    W, H, N = 120, 96, 14
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (12, 28, 20, 255))
        d = ImageDraw.Draw(im)
        for y in range(H):
            if y % 3 == 0:
                d.line([(0, y), (W, y)], fill=(0, 0, 0, 25))
        rect(d, [0, 70, W, H], fill=(20, 60, 36, 255))
        for tx in (12, 100):
            rect(d, [tx, 50, tx + 4, 70], fill=(60, 40, 20, 255))
            d.ellipse(
                [tx - 8, 34, tx + 12, 58],
                fill=(0, 160, 60, 255),
                outline=(0, 228, 54, 255),
            )
        cx = 60
        rect(d, [cx - 4, 28, cx + 4, 70], fill=(180, 255, 210, 255), outline=(0, 228, 54, 255))
        d.polygon(
            [(cx, 12), (cx - 10, 30), (cx + 10, 30)],
            fill=(0, 228, 54, 255),
            outline=(255, 255, 255, 200),
        )
        for r in range(3):
            rr = 8 + r * 8 + (i % 7)
            d.ellipse(
                [cx - rr, 40 - rr // 2, cx + rr, 40 + rr // 2],
                outline=(0, 228, 54, 90 - r * 20),
            )
        for k in range(10):
            h = 6 + int(10 + 8 * math.sin((i + k) / 2.2))
            x = 18 + k * 9
            rect(
                d,
                [x, 86 - h, x + 5, 86],
                fill=(0, 228, 54, 255),
                outline=(0, 80, 30, 255),
            )
        for s in range(6):
            ang = (i * 20 + s * 60) % 360
            rad = 18 + (s % 3) * 4
            sx = cx + int(math.cos(math.radians(ang)) * rad)
            sy = 34 + int(math.sin(math.radians(ang)) * rad / 2)
            if 0 <= sx < W and 0 <= sy < H:
                d.point((sx, sy), fill=(255, 236, 39, 255))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "ok.gif"), 85)


def make_err():
    W, H, N = 120, 96, 12
    frames = []
    for i in range(N):
        rng = random.Random(11 + i)
        im = Image.new("RGBA", (W, H), (28, 10, 16, 255))
        d = ImageDraw.Draw(im)
        flash = i % 2 == 0
        border = (255, 0, 77, 255) if flash else (120, 0, 30, 255)
        rect(d, [0, 0, W - 1, H - 1], outline=border)
        for x in range(0, W, 12):
            off = (i * 3) % 12
            rect(d, [x - off, 0, x - off + 6, 8], fill=(255, 236, 39, 255))
            rect(d, [x - off + 6, 0, x - off + 12, 8], fill=(26, 28, 44, 255))
        rect(d, [50, 22, 70, 40], fill=(80, 0, 20, 255), outline=(255, 0, 77, 255))
        d.ellipse(
            [48, 12, 72, 30],
            fill=(255, 0, 77, 255) if flash else (180, 0, 40, 255),
        )
        if flash:
            d.polygon([(60, 24), (10, 70), (30, 70)], fill=(255, 0, 77, 50))
            d.polygon([(60, 24), (90, 70), (110, 70)], fill=(255, 0, 77, 50))
        rect(d, [20, 48, 100, 86], fill=(10, 10, 14, 255), outline=(255, 0, 77, 200))
        for y in range(54, 82, 3):
            if rng.random() > 0.35:
                x2 = rng.randint(30, 95)
                d.line([(26, y), (x2, y)], fill=(255, 0, 77, 160))
        for _ in range(8):
            x = rng.randint(5, W - 15)
            y = rng.randint(10, H - 10)
            w = rng.randint(4, 16)
            h = rng.randint(1, 3)
            rect(d, [x, y, x + w, y + h], fill=(255, 119, 168, 100))
        for bx in range(5):
            rect(
                d,
                [28 + bx * 12, 56, 36 + bx * 12, 64],
                fill=(255, 236, 39, 255) if flash else (255, 0, 77, 255),
            )
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "err.gif"), 100)


def make_detail():
    W, H, N = 220, 160, 16
    frames = []
    for i in range(N):
        im = Image.new("RGBA", (W, H), (12, 12, 22, 255))
        d = ImageDraw.Draw(im)
        rect(d, [10, 10, 210, 90], fill=(16, 20, 40, 255), outline=(41, 173, 255, 180))
        for bi, (x, h) in enumerate(
            [
                (18, 40),
                (34, 55),
                (50, 35),
                (66, 60),
                (84, 45),
                (102, 65),
                (120, 38),
                (138, 58),
                (156, 42),
                (174, 62),
                (192, 36),
            ]
        ):
            rect(d, [x, 90 - h, x + 12, 90], fill=(26, 28, 44, 255), outline=(41, 173, 255, 80))
            for wy in range(90 - h + 4, 88, 6):
                if ((wy // 6) + i + bi) % 3 == 0:
                    d.point((x + 4, wy), fill=(255, 236, 39, 255))
                    d.point((x + 8, wy), fill=(41, 173, 255, 255))
        rng = random.Random(5)
        for _ in range(30):
            x = rng.randint(14, 206)
            y = rng.randint(14, 70)
            if (x + y + i) % 4:
                d.point((x, y), fill=(200, 210, 255, 200))
        rect(d, [0, 110, W, H], fill=(30, 24, 44, 255))
        rect(d, [0, 108, W, 110], fill=(255, 236, 39, 200))
        for mx in (36, 120):
            rect(d, [mx, 70, mx + 64, 112], fill=(8, 10, 16, 255), outline=(255, 236, 39, 255))
            for c in range(8):
                for r in range(6):
                    y = 76 + r * 5 + (i + c) % 3
                    if y < 108:
                        col = (0, 228, 54, 200) if (r + i + c) % 3 else (41, 173, 255, 160)
                        rect(d, [mx + 6 + c * 7, y, mx + 10 + c * 7, y + 2], fill=col)
        rect(d, [70, 124, 150, 138], fill=(20, 20, 28, 255), outline=(180, 180, 200, 255))
        for k in range(10):
            rect(d, [76 + k * 7, 128, 80 + k * 7, 132], fill=(60, 60, 80, 255))
        # original pixel cat silhouette
        d.ellipse([168, 118, 198, 140], fill=(40, 40, 50, 255), outline=(255, 119, 168, 200))
        d.ellipse([186, 108, 208, 128], fill=(40, 40, 50, 255), outline=(255, 119, 168, 200))
        d.polygon([(188, 112), (192, 100), (196, 112)], fill=(40, 40, 50, 255))
        d.polygon([(198, 112), (204, 100), (208, 112)], fill=(40, 40, 50, 255))
        if i % 8 != 3:
            rect(d, [192, 116, 194, 118], fill=(255, 236, 39, 255))
            rect(d, [200, 116, 202, 118], fill=(255, 236, 39, 255))
        for fi, x in enumerate((20, 80, 140)):
            on = (i + fi) % 4 != 0
            rect(
                d,
                [x, 148, x + 40, 156],
                fill=(0, 228, 54, 255) if on else (60, 60, 80, 255),
                outline=(0, 0, 0, 255),
            )
        for y in range(0, H, 3):
            d.line([(0, y), (W, y)], fill=(0, 0, 0, 28))
        frames.append(quant(im))
    save_gif(frames, os.path.join(OUT, "detail.gif"), 90)


if __name__ == "__main__":
    make_rate()
    make_nodes()
    make_ok()
    make_err()
    make_detail()
    print("done")
