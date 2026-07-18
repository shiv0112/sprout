"""
generate_qr.py
--------------
Generate a QR code for any text or URL and return it as a
base64-encoded PNG data URL, ready to embed in HTML or save to disk.
"""

import base64
import io
import os
import tempfile


def generate_qr(content: str, size: int = 10) -> dict:
    """
    Generate a QR code for the given content.

    Args:
        content: Text or URL to encode.
        size:    Box size in pixels per QR module (default 10).

    Returns:
        dict with keys: content, data_url, file_path, success
    """
    try:
        import qrcode
        from PIL import Image

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=size,
            border=4,
        )
        qr.add_data(content)
        qr.make(fit=True)

        img: Image.Image = qr.make_image(fill_color="black", back_color="white")

        # Encode to base64
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        data_url = f"data:image/png;base64,{b64}"

        # Save to temp file
        tmp = tempfile.NamedTemporaryFile(
            suffix=".png", prefix="qr_", delete=False
        )
        img.save(tmp.name)
        tmp.close()

        return {
            "content": content,
            "data_url": data_url,
            "file_path": tmp.name,
            "success": True,
        }

    except Exception as exc:
        return {
            "content": content,
            "data_url": "",
            "file_path": "",
            "success": False,
            "error": str(exc),
        }
