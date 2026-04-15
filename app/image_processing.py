"""Server-side image processing for battle cards.

Handles:
- Compositing AI-generated character onto template card backgrounds
- Text overlay (card name + location) at the bottom of cards
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


def apply_text_overlay(
    image_bytes: bytes,
    card_name: str,
    location: str,
    card_index: int,
) -> bytes:
    """Apply card name and location text overlay to the bottom of a generated image.

    The card name is displayed in a larger font, and the location in a smaller
    font below it. Both are centered horizontally. A semi-transparent dark
    banner is drawn behind the text for readability.

    Args:
        image_bytes: The AI-generated card image (PNG bytes).
        card_name: The generated battle card name (from naming logic).
        location: The location name to display.
        card_index: Card index (1-6) for theme-aware styling.

    Returns:
        The final composited image as PNG bytes.
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")

    # Resize to card dimensions if needed
    if img.size != (CARD_WIDTH, CARD_HEIGHT):
        img = img.resize((CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)

    # Font sizes
    name_font_size = 56
    location_font_size = 36
    name_font = _get_font(name_font_size)
    location_font = _get_font(location_font_size)

    # Create a temporary draw to measure text
    temp_draw = ImageDraw.Draw(img)

    # Measure text dimensions
    name_bbox = temp_draw.textbbox((0, 0), card_name, font=name_font)
    name_w = name_bbox[2] - name_bbox[0]
    name_h = name_bbox[3] - name_bbox[1]

    loc_bbox = temp_draw.textbbox((0, 0), location, font=location_font)
    loc_w = loc_bbox[2] - loc_bbox[0]
    loc_h = loc_bbox[3] - loc_bbox[1]

    # If the card name is too wide, reduce font size
    if name_w > CARD_WIDTH - 80:
        name_font_size = int(name_font_size * (CARD_WIDTH - 80) / name_w)
        name_font = _get_font(name_font_size)
        name_bbox = temp_draw.textbbox((0, 0), card_name, font=name_font)
        name_w = name_bbox[2] - name_bbox[0]
        name_h = name_bbox[3] - name_bbox[1]

    # Banner dimensions
    padding_x = 40
    padding_y = 20
    spacing = 10  # between name and location
    total_text_h = name_h + spacing + loc_h
    banner_h = total_text_h + padding_y * 2
    banner_y = CARD_HEIGHT - banner_h - 30  # 30px from bottom edge

    # Draw semi-transparent dark banner
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        [padding_x, banner_y, CARD_WIDTH - padding_x, banner_y + banner_h],
        radius=16,
        fill=(0, 0, 0, 160),
    )
    img = Image.alpha_composite(img, overlay)

    # Draw text
    draw = ImageDraw.Draw(img)

    # Card name (centered, white, bold look)
    name_x = (CARD_WIDTH - name_w) // 2
    name_y = banner_y + padding_y
    # Draw slight shadow for depth
    draw.text((name_x + 2, name_y + 2), card_name, font=name_font, fill=(0, 0, 0, 200))
    draw.text((name_x, name_y), card_name, font=name_font, fill=(255, 255, 255, 255))

    # Location (centered, slightly smaller, light gray)
    loc_x = (CARD_WIDTH - loc_w) // 2
    loc_y = name_y + name_h + spacing
    draw.text((loc_x + 1, loc_y + 1), location, font=location_font, fill=(0, 0, 0, 180))
    draw.text((loc_x, loc_y), location, font=location_font, fill=(220, 220, 220, 255))

    # Convert back to RGB for PNG output
    final = img.convert("RGB")
    buf = BytesIO()
    final.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
