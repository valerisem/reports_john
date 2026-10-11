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
    """A pie with each slice named beside it: one straight line out to small text.

    ``rows`` are ``(label, value, "#rrggbb")``, drawn clockwise from twelve
    o'clock. Each line points straight out from the middle of its slice and
    the label sits at its end, giving the slice's share only. Where two
    labels would collide the later line is drawn longer, never bent. Returns
    the PNG and the width and height to show it at (the file is ``scale``
    times that).
    """
    rows = [row for row in rows if row[1] > 0]
    total = sum(value for _, value, _ in rows)
    measure = ImageFont.load_default(size=font_px)
    line_h = round(font_px * 1.25)
    pad = 3  # clear space kept around each label

    # Lay out in pie-centred coordinates, at the size it will be shown.
    labels = []
    angle = -90.0
    for label, value, _ in rows:
        sweep = 360.0 * value / total
        mid = math.radians(angle + sweep / 2)
        lines = _wrap(label, wrap)
        share = _share(value, total)
        widths = [measure.getlength(line + ("  " + share if i == len(lines) - 1 else ""))
                  for i, line in enumerate(lines)]
        labels.append({"mid": mid, "lines": lines, "share": share,
                       "right": math.cos(mid) >= 0, "w": max(widths),
                       "h": line_h * len(lines)})
        angle += sweep

    placed: list[tuple[float, float, float, float]] = []
    pie_box = (-radius, -radius, radius, radius)

    def clashes(box):
        return any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3])
                   for b in placed)

    for l in labels:
        cos, sin = math.cos(l["mid"]), math.sin(l["mid"])
        reach = 14
        while True:
            end = ((radius + reach) * cos, (radius + reach) * sin)
            x0 = end[0] + 4 if l["right"] else end[0] - 4 - l["w"]
            box = (x0 - pad, end[1] - l["h"] / 2 - pad, x0 + l["w"] + pad, end[1] + l["h"] / 2 + pad)
            inside_pie = not (box[2] < pie_box[0] or box[0] > pie_box[2]
                              or box[3] < pie_box[1] or box[1] > pie_box[3]) and \
                min(abs(box[0]), abs(box[2])) < radius * abs(cos) + 2
            if (not clashes(box) and not inside_pie) or reach > 4 * radius:
                break
            reach += 4
        l["end"], l["x0"], l["box"] = end, x0, box
        placed.append(box)

    left = min([b[0] for b in placed] + [-radius]) - 2
    right = max([b[2] for b in placed] + [radius]) + 2
    top = min([b[1] for b in placed] + [-radius]) - 2
    bottom = max([b[3] for b in placed] + [radius]) + 2
    # keep the pie in the middle of the picture so it lines up with the page
    half = max(-left, right)
    width, height = int(2 * half), int(bottom - top)
    cx, cy = half, -top

    # Everything is drawn SUPERSAMPLE times larger again and shrunk at the
    # end, which is what keeps the thin lines and the text smooth.
    k = scale * SUPERSAMPLE
    image = Image.new("RGBA", (width * k, height * k), (255, 255, 255, 0))
    pie = Image.open(BytesIO(pie_png([(v, c) for _, v, c in rows], size=2 * radius * k)))
    image.alpha_composite(pie, (round((cx - radius) * k), round((cy - radius) * k)))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px * k)
    for l in labels:
        cos, sin = math.cos(l["mid"]), math.sin(l["mid"])
        start = (cx + (radius + 2) * cos, cy + (radius + 2) * sin)
        end = (cx + l["end"][0], cy + l["end"][1])
        draw.line([(start[0] * k, start[1] * k), (end[0] * k, end[1] * k)], fill=RULE, width=k)
        for i, line in enumerate(l["lines"]):
            last = i == len(l["lines"]) - 1
            name, share = (line + "  ", l["share"]) if last else (line, "")
            text_w = draw.textlength(name + share, font=font) / k
            x = cx + l["x0"] + (0 if l["right"] else l["w"] - text_w)
            y = cy + l["end"][1] - l["h"] / 2 + i * line_h + (line_h - font_px) / 2 - 1
            draw.text((x * k, y * k), name, font=font, fill=LABEL)
            if share:
                draw.text(((x + draw.textlength(name, font=font) / k) * k, y * k), share,
                          font=font, fill=FIGURE)
    image = image.resize((width * scale, height * scale), Image.LANCZOS)

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue(), width, height
