"""Render the Quill app icon (.icns) natively with AppKit.

Wispr-Flow-adjacent branding: warm paper squircle, ink-dark feather, coral dot.
Usage: uv run python scripts/make_icon.py <output-dir>
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSShadow,
)
from Foundation import NSString

CANVAS = 1024
# Apple's Big-Sur-style icon grid: squircle fills ~80% of the canvas
SQUIRCLE = 824
RADIUS = 186


def draw_icon() -> NSImage:
    image = NSImage.alloc().initWithSize_((CANVAS, CANVAS))
    image.lockFocus()

    inset = (CANVAS - SQUIRCLE) / 2
    rect = NSMakeRect(inset, inset, SQUIRCLE, SQUIRCLE)
    squircle = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, RADIUS, RADIUS)

    # soft drop shadow behind the squircle
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0, 0, 0, 0.30))
    shadow.setShadowOffset_((0, -14))
    shadow.setShadowBlurRadius_(28)
    NSGraphicsContext.currentContext().saveGraphicsState()
    shadow.set()
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.97, 0.95, 0.91, 1.0).setFill()
    squircle.fill()
    NSGraphicsContext.currentContext().restoreGraphicsState()

    # warm paper gradient
    gradient = NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.99, 0.97, 0.93, 1.0),
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.94, 0.90, 0.83, 1.0),
    )
    gradient.drawInBezierPath_angle_(squircle, -90)

    # the feather (Apple Color Emoji renders via the system font)
    glyph = NSString.stringWithString_("🪶")
    attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(540)}
    size = glyph.sizeWithAttributes_(attrs)
    glyph.drawAtPoint_withAttributes_(
        (CANVAS / 2 - size.width / 2, CANVAS / 2 - size.height / 2 + 30), attrs
    )

    # coral dot — the "period" the quill just wrote
    NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.45, 0.25, 1.0).setFill()
    dot = 74
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(CANVAS / 2 + 150, inset + 148, dot, dot)
    ).fill()

    image.unlockFocus()
    return image


def save_png(image: NSImage, path: Path) -> None:
    tiff = image.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
    png.writeToFile_atomically_(str(path), True)


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        master = Path(tmp) / "master.png"
        save_png(draw_icon(), master)

        for size in (16, 32, 64, 128, 256, 512, 1024):
            for scale in (1, 2):
                px = size * scale
                if px > 1024:
                    continue
                suffix = "" if scale == 1 else "@2x"
                name = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(
                    ["sips", "-z", str(px), str(px), str(master), "--out", str(name)],
                    capture_output=True, check=True,
                )

        icns = out_dir / "AppIcon.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
        print(f"Wrote {icns}")


if __name__ == "__main__":
    main()
