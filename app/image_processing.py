"""Server-side image processing for battle cards.

Handles:
- Compositing AI-generated character onto template card backgrounds
- Text overlay (first name + location) placed inside template text boxes
"""

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# Card dimensions (63mm x 88mm at 600 DPI)
CARD_WIDTH = 1488
CARD_HEIGHT = 2079

# Template directory
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Template file mapping: card_index -> filename
TEMPLATE_FILES: dict[int, str] = {
    1: "fire.png",
    2: "water.png",
    3: "thunder.png",
    4: "nature.png",
    5: "void.png",
    6: "light.png",
}

# Font paths (IPA Gothic for Japanese text)
_FONT_PATHS = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",  # IPA P Gothic
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",   # IPA Gothic
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",  # fallback
]


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a Japanese-capable font at the given size."""
    for path in _FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    # Last resort: default font (may not support Japanese)
    return ImageFont.load_default()


def get_template_path(card_index: int) -> str | None:
    """Get the file path for a template image by card index."""
    filename = TEMPLATE_FILES.get(card_index)
    if not filename:
        return None
    path = os.path.join(TEMPLATES_DIR, filename)
    return path if os.path.exists(path) else None


def get_template_bytes(card_index: int) -> bytes | None:
    """Load template image bytes for sending to the AI API."""
    path = get_template_path(card_index)
    if not path:
        return None
    with open(path, "rb") as f:
        return f.read()


# Text box regions on the template (in production 1488x2079 coordinates).
# Upper box: location name
_LOCATION_BOX = {"x1": 340, "y1": 1630, "x2": 1150, "y2": 1680}
# Lower box: user's first name
_NAME_BOX = {"x1": 340, "y1": 1790, "x2": 1150, "y2": 1920}


def _fit_font_size(
    text: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int = 12,
) -> ImageFont.FreeTypeFont:
    """Find the largest font size that fits *text* inside the given bounds."""
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    for size in range(start_size, min_size - 1, -1):
        font = _get_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        if tw <= max_width and th <= max_height:
            return font
    return _get_font(min_size)


def apply_text_overlay(
    image_bytes: bytes,
    first_name: str,
    location: str,
    card_index: int,
) -> bytes:
    """Place location and first name into the template's text boxes.

    The template image has two pre-designed boxes at the bottom:
      - Upper box: location name (smaller text)
      - Lower box: user's first name (larger text)
    Text is centred horizontally and vertically within each box.
    No banner is drawn; the template already provides the background.

    Args:
        image_bytes: The AI-generated card image (PNG bytes).
        first_name: The user's given name to display.
        location: The location name to display.
        card_index: Card index (1-6), reserved for future per-theme tweaks.

    Returns:
        The final composited image as PNG bytes.
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")

    # Resize to card dimensions if needed
    if img.size != (CARD_WIDTH, CARD_HEIGHT):
        img = img.resize((CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)

    draw = ImageDraw.Draw(img)

    # --- Upper box: location ---
    loc_box_w = _LOCATION_BOX["x2"] - _LOCATION_BOX["x1"]
    loc_box_h = _LOCATION_BOX["y2"] - _LOCATION_BOX["y1"]
    loc_font = _fit_font_size(location, loc_box_w, loc_box_h, start_size=40)
    loc_bbox = draw.textbbox((0, 0), location, font=loc_font)
    loc_tw = loc_bbox[2] - loc_bbox[0]
    loc_th = loc_bbox[3] - loc_bbox[1]
    loc_x = _LOCATION_BOX["x1"] + (loc_box_w - loc_tw) // 2 - loc_bbox[0]
    loc_y = _LOCATION_BOX["y1"] + (loc_box_h - loc_th) // 2 - loc_bbox[1]
    # Shadow for readability
    draw.text((loc_x + 1, loc_y + 1), location, font=loc_font, fill=(0, 0, 0, 180))
    draw.text((loc_x, loc_y), location, font=loc_font, fill=(255, 255, 255, 255))

    # --- Lower box: first name ---
    name_box_w = _NAME_BOX["x2"] - _NAME_BOX["x1"]
    name_box_h = _NAME_BOX["y2"] - _NAME_BOX["y1"]
    name_font = _fit_font_size(first_name, name_box_w, name_box_h, start_size=90)
    name_bbox = draw.textbbox((0, 0), first_name, font=name_font)
    name_tw = name_bbox[2] - name_bbox[0]
    name_th = name_bbox[3] - name_bbox[1]
    name_x = _NAME_BOX["x1"] + (name_box_w - name_tw) // 2 - name_bbox[0]
    name_y = _NAME_BOX["y1"] + (name_box_h - name_th) // 2 - name_bbox[1]
    # Shadow for readability
    draw.text((name_x + 2, name_y + 2), first_name, font=name_font, fill=(0, 0, 0, 200))
    draw.text((name_x, name_y), first_name, font=name_font, fill=(255, 255, 255, 255))

    # Convert back to RGB for PNG output
    final = img.convert("RGB")
    buf = BytesIO()
    final.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
