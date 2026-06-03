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
# 水テーマは一旦使用しない（5枚生成+広告モード固定のため）
# 旧マッピング:
#   1: "fire.png", 2: "water.png", 3: "thunder.png",
#   4: "nature.png", 5: "void.png", 6: "light.png"
TEMPLATE_FILES: dict[int, str] = {
    1: "fire.png",
    # 2: "water.png",  # 水テーマは一旦使用しない
    2: "thunder.png",
    3: "nature.png",
    4: "void.png",
    5: "light.png",
}

# Ad card background image
AD_TEMPLATE_FILE = "advertisment.png"

# Font directory bundled with this package
_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Font search paths: bundled font first, then OS-specific paths
_FONT_PATHS = [
    # Bundled font (works on all platforms)
    os.path.join(_FONTS_DIR, "NotoSansJP.ttf"),
    # Linux (IPA Gothic)
    "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    # macOS (Hiragino)
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    "C:/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/yugothb.ttf",
]


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a Japanese-capable font at the given size."""
    for path in _FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    # Pillow 10.1+ supports sized default font
    return ImageFont.load_default(size=size)


def generate_ad_card(
    message: str,
    store_name: str,
    company_name: str,
) -> bytes:
    """Generate an advertisement card from the advertisment.png template.

    The ad card uses the advertisment.png as a landscape background,
    with a semi-transparent white rectangle in the center containing
    the message, store name, and company name.

    Returns the ad card image as PNG bytes.
    """
    ad_path = os.path.join(TEMPLATES_DIR, AD_TEMPLATE_FILE)
    if not os.path.exists(ad_path):
        raise FileNotFoundError(f"Ad template not found: {ad_path}")

    img = Image.open(ad_path).convert("RGBA")

    # Ensure landscape orientation (width > height)
    if img.width < img.height:
        img = img.transpose(Image.ROTATE_90)

    draw = ImageDraw.Draw(img)

    # Font sizes scaled proportionally to image height
    msg_font_size = max(36, img.height // 10)    # ~10% of height
    sub_font_size = max(28, img.height // 13)     # ~7.5% of height

    # Build text lines
    lines: list[tuple[str, int]] = []  # (text, font_size)
    if message:
        lines.append((message, msg_font_size))
    if store_name:
        lines.append((f"店舗名：{store_name}", sub_font_size))
    if company_name:
        lines.append((f"会社名：{company_name}", sub_font_size))

    if not lines:
        # No text to overlay; return image as-is
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # Calculate text area dimensions (scaled to image size)
    padding_x = max(40, img.width // 20)
    padding_y = max(24, img.height // 20)
    line_spacing = max(16, img.height // 30)
    fonts: list[ImageFont.FreeTypeFont] = []
    text_widths: list[int] = []
    text_heights: list[int] = []

    dummy = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy)

    for text, size in lines:
        max_w = int(img.width * 0.8) - 2 * padding_x
        max_h = size + 20
        font = _fit_font_size(text, max_w, max_h, start_size=size, min_size=20)
        fonts.append(font)
        bbox = dummy_draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        text_widths.append(tw)
        text_heights.append(th)

    total_text_h = sum(text_heights) + line_spacing * (len(lines) - 1)
    max_text_w = max(text_widths)

    box_w = max_text_w + 2 * padding_x
    box_h = total_text_h + 2 * padding_y
    box_x = (img.width - box_w) // 2
    box_y = (img.height - box_h) // 2

    # Draw semi-transparent white rectangle
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        fill=(255, 255, 255, 220),
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Draw text lines centered in the box
    current_y = box_y + padding_y
    for i, (text, _size) in enumerate(lines):
        font = fonts[i]
        tw = text_widths[i]
        tx = (img.width - tw) // 2
        bbox = dummy_draw.textbbox((0, 0), text, font=font)
        ty_offset = bbox[1]
        draw.text((tx - bbox[0], current_y - ty_offset), text, font=font, fill=(0, 0, 0, 255))
        current_y += text_heights[i] + line_spacing

    final = img.convert("RGB")
    buf = BytesIO()
    final.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


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


# Text banner region on the latest template set (in 1488x2079 coordinates).
# The new backgrounds use a single wide banner near the bottom, so both the
# location and first name are laid out inside this shared frame.
_TEXT_BANNER_BOX = {"x1": 185, "y1": 1570, "x2": 1303, "y2": 1730}
_TEXT_BANNER_PADDING_X = 24
_TEXT_BANNER_PADDING_Y = 12
_LOCATION_HEIGHT_RATIO = 0.42


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
    """Place location and first name into the template's bottom banner.

    The latest template set has a single wide frame near the bottom. The
    overlay splits that frame into two stacked text areas:
      - Upper area: location name (smaller text)
      - Lower area: user's first name (larger text)
    Text is centred horizontally and vertically within each area.
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

    banner_x1 = _TEXT_BANNER_BOX["x1"] + _TEXT_BANNER_PADDING_X
    banner_y1 = _TEXT_BANNER_BOX["y1"] + _TEXT_BANNER_PADDING_Y
    banner_x2 = _TEXT_BANNER_BOX["x2"] - _TEXT_BANNER_PADDING_X
    banner_y2 = _TEXT_BANNER_BOX["y2"] - _TEXT_BANNER_PADDING_Y
    banner_w = banner_x2 - banner_x1
    banner_h = banner_y2 - banner_y1
    split_y = banner_y1 + int(banner_h * _LOCATION_HEIGHT_RATIO)

    location_box = {"x1": banner_x1, "y1": banner_y1, "x2": banner_x2, "y2": split_y}
    name_box = {"x1": banner_x1, "y1": split_y, "x2": banner_x2, "y2": banner_y2}

    # --- Upper area: location ---
    loc_box_w = location_box["x2"] - location_box["x1"]
    loc_box_h = location_box["y2"] - location_box["y1"]
    loc_font = _fit_font_size(location, loc_box_w, loc_box_h, start_size=56, min_size=22)
    loc_bbox = draw.textbbox((0, 0), location, font=loc_font)
    loc_tw = loc_bbox[2] - loc_bbox[0]
    loc_th = loc_bbox[3] - loc_bbox[1]
    loc_x = location_box["x1"] + (loc_box_w - loc_tw) // 2 - loc_bbox[0]
    loc_y = location_box["y1"] + (loc_box_h - loc_th) // 2 - loc_bbox[1]
    draw.text(
        (loc_x, loc_y),
        location,
        font=loc_font,
        fill=(60, 35, 24, 255),
        stroke_width=2,
        stroke_fill=(255, 255, 255, 180),
    )

    # --- Lower area: first name ---
    name_box_w = name_box["x2"] - name_box["x1"]
    name_box_h = name_box["y2"] - name_box["y1"]
    name_font = _fit_font_size(first_name, name_box_w, name_box_h, start_size=88, min_size=30)
    name_bbox = draw.textbbox((0, 0), first_name, font=name_font)
    name_tw = name_bbox[2] - name_bbox[0]
    name_th = name_bbox[3] - name_bbox[1]
    name_x = name_box["x1"] + (name_box_w - name_tw) // 2 - name_bbox[0]
    name_y = name_box["y1"] + (name_box_h - name_th) // 2 - name_bbox[1]
    draw.text(
        (name_x, name_y),
        first_name,
        font=name_font,
        fill=(35, 20, 15, 255),
        stroke_width=3,
        stroke_fill=(255, 255, 255, 200),
    )

    # Convert back to RGB for PNG output
    final = img.convert("RGB")
    buf = BytesIO()
    final.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
