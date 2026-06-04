"""Cloud Vision AI face validation for battle card photos.

Validates that:
1. Exactly one person (face) is detected in the image.
2. The person's face is oriented toward the camera (frontal).

Requires GOOGLE_APPLICATION_CREDENTIALS environment variable pointing
to a service account JSON key file (e.g. credentials/gcp-vision-sa.json).
"""

from dataclasses import dataclass

from google.cloud import vision


# Maximum acceptable head rotation angles (degrees) for "facing forward".
_MAX_PAN_ANGLE = 25.0  # left-right (yaw)
_MAX_TILT_ANGLE = 25.0  # up-down (pitch)


@dataclass
class ValidationResult:
    valid: bool
    error: str | None = None


def validate_face(image_bytes: bytes) -> ValidationResult:
    """Validate that the image contains exactly one front-facing person.

    Args:
        image_bytes: Raw image bytes (JPEG or PNG).

    Returns:
        ValidationResult with ``valid=True`` if checks pass, or
        ``valid=False`` with a Japanese error message describing the issue.
    """
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)

    response = client.face_detection(image=image)

    if response.error.message:
        return ValidationResult(
            valid=False,
            error=f"画像の解析に失敗しました: {response.error.message}",
        )

    faces = response.face_annotations

    if len(faces) == 0:
        return ValidationResult(
            valid=False,
            error="人物の顔が検出されませんでした。正面を向いて撮り直してください。",
        )

    if len(faces) > 1:
        return ValidationResult(
            valid=False,
            error="複数の人物が検出されました。一人で撮り直してください。",
        )

    face = faces[0]

    pan = abs(face.pan_angle)
    tilt = abs(face.tilt_angle)

    if pan > _MAX_PAN_ANGLE or tilt > _MAX_TILT_ANGLE:
        return ValidationResult(
            valid=False,
            error="顔が正面を向いていません。カメラに向かって正面を向いて撮り直してください。",
        )

    return ValidationResult(valid=True)
