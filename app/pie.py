"""Pie charts as PNG images, for the email.

Email clients will not draw SVG or CSS conic gradients reliably, so a pie
has to be a picture. It travels inside the email as an inline attachment
(see ``mailer.build_message``). Each slice is named beside the pie, with a
thin line out to small text giving its share.
"""
from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

SIZE = 360       # pixels in the file; shown at half that for sharp phones
SUPERSAMPLE = 4  # drawn large and shrunk, which smooths the edges

LABEL = "#6b6a75"
FIGURE = "#15141f"
RULE = "#9a99a5"


def pie_png(slices: list[tuple[float, str]], *, size: int = SIZE) -> bytes:
    """A pie of ``(value, "#rrggbb")`` slices, clockwise from twelve o'clock."""
    total = sum(value for value, _ in slices if value > 0)
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    box = (0, 0, big - 1, big - 1)
    if total <= 0:
        draw.ellipse(box, fill="#e7e5ec")
    else:
        start = -90.0
        for value, colour in slices:
            if value <= 0:
                continue
            sweep = 360.0 * value / total
            draw.pieslice(box, start, start + sweep, fill=colour)
            start += sweep
        if len([v for v, _ in slices if v > 0]) > 1:
            # thin white seams between slices, so neighbours never blur together
            start = -90.0
            for value, _ in slices:
                if value <= 0:
                    continue
                start += 360.0 * value / total
                draw.pieslice(box, start - 0.6, start + 0.6, fill="#ffffff")
    image = image.resize((size, size), Image.LANCZOS)
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _wrap(text: str, limit: int) -> list[str]:
    """One line, or two broken at the space nearest the middle."""
    if len(text) <= limit or " " not in text:
        return [text]
    if " / " in text:  # "No response / went cold" breaks after the slash
        head, tail = text.split(" / ", 1)
        return [head + " /", tail]
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    cut = min(spaces, key=lambda i: abs(i - len(text) / 2))
    return [text[:cut], text[cut + 1:]]


def _share(value: float, total: float) -> str:
    pct = value / total * 100
    return "<1%" if 0 < pct < 1 else f"{round(pct)}%"


def labelled_pie_png(rows: list[tuple[str, float, str]], *, radius: int = 66,
                     font_px: int = 13, wrap: int = 14,
                     scale: int = 2) -> tuple[bytes, int, int]:
    """A pie with each slice named beside it: a thin line out to small text.

    ``rows`` are ``(label, value, "#rrggbb")``, drawn clockwise from twelve
    o'clock. Each label gives the slice's share only. Returns the PNG and the
    width and height to show it at (the file is ``scale`` times that).
    """
    rows = [row for row in rows if row[1] > 0]
    total = sum(value for _, value, _ in rows)
    font = ImageFont.load_default(size=font_px * scale)
    line_h = round(font_px * 1.25)
    gap = 5

    labels = []
    angle = -90.0
    for label, value, _ in rows:
        sweep = 360.0 * value / total
        mid = math.radians(angle + sweep / 2)
        lines = _wrap(label, wrap)
        labels.append({"mid": mid, "lines": lines, "share": _share(value, total),
                       "right": math.cos(mid) >= 0, "h": line_h * len(lines)})
        angle += sweep

    # Each side's labels, top to bottom, as near as they can sit to where
    # their slice points without overlapping. Positions are relative to the
    # centre of the pie; the picture is sized around them afterwards.
    for side in (True, False):
        group = sorted((l for l in labels if l["right"] == side), key=lambda l: math.sin(l["mid"]))
        floor = -math.inf
        for l in group:
            # ``y`` is the label's vertical middle, where its line arrives
            l["y"] = max((radius + 12) * math.sin(l["mid"]), floor + l["h"] / 2)
            floor = l["y"] + l["h"] / 2 + gap
        if group:
            # a stack pushed off the bottom slides back up, as far as there is room
            overflow = group[-1]["y"] - (radius + 6)
            room = group[0]["y"] - (-radius - 6)
            for l in group:
                l["y"] -= min(max(overflow, 0), max(room, 0))

    top = min([l["y"] - l["h"] / 2 for l in labels] + [-radius]) - 6
    bottom = max([l["y"] + l["h"] / 2 for l in labels] + [radius]) + 6
    longest = max((font.getlength(line + ("  " + l["share"] if i == len(l["lines"]) - 1 else "")) / scale
                   for l in labels for i, line in enumerate(l["lines"])), default=0)
    reach = radius + 18 + 14 + 5          # pie edge, elbow, tail, gap before text
    width = int(2 * (reach + longest + 4))
    height = int(bottom - top)
    cx, cy = width / 2, -top

    s = scale
    image = Image.new("RGBA", (width * s, height * s), (255, 255, 255, 0))
    pie = Image.open(BytesIO(pie_png([(v, c) for _, v, c in rows], size=2 * radius * s)))
    image.alpha_composite(pie, (round((cx - radius) * s), round((cy - radius) * s)))

    draw = ImageDraw.Draw(image)
    for l in labels:
        side = 1 if l["right"] else -1
        y = cy + l["y"]
        cos, sin = math.cos(l["mid"]), math.sin(l["mid"])
        rim = (cx + radius * cos, cy + radius * sin)
        stub = (cx + (radius + 8) * cos, cy + (radius + 8) * sin)
        elbow_x = cx + side * max(radius + 18, side * (stub[0] - cx))
        end_x = elbow_x + side * 14
        draw.line([(rim[0] * s, rim[1] * s), (stub[0] * s, stub[1] * s),
                   (elbow_x * s, y * s), (end_x * s, y * s)], fill=RULE, width=s)

        for i, line in enumerate(l["lines"]):
            last = i == len(l["lines"]) - 1
            name, share = (line + "  ", l["share"]) if last else (line, "")
            text_w = draw.textlength(name + share, font=font)
            x = (end_x + 5) * s if l["right"] else (end_x - 5) * s - text_w
            text_top = (y - l["h"] / 2 + i * line_h + (line_h - font_px) / 2 - 1) * s
            draw.text((x, text_top), name, font=font, fill=LABEL)
            if share:
                draw.text((x + draw.textlength(name, font=font), text_top), share,
                          font=font, fill=FIGURE)

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue(), width, height
