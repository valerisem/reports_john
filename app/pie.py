"""Pie charts as PNG images, for the email.

Email clients will not draw SVG or CSS conic gradients reliably, so a pie
has to be a picture. It travels inside the email as an inline attachment
(see ``mailer.build_message``) and the legend stays as HTML text, so it is
readable and searchable even where images are blocked.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

SIZE = 360       # pixels in the file; shown at half that for sharp phones
SUPERSAMPLE = 4  # drawn large and shrunk, which smooths the edges


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
