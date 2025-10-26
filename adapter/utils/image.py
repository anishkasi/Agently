from PIL import Image
import io


async def normalize_image(file_bytes: bytes) -> tuple[bytes, str]:
    """Normalize an image to a JPEG or PNG format."""
    header = file_bytes[:8]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return file_bytes, "png"
    if header.startswith(b"\xff\xd8\xff"):
        return file_bytes, "jpg"
    if file_bytes[:6] in (b"GIF87a", b"GIF89a"):
        img = Image.open(io.BytesIO(file_bytes))
        try:
            img.seek(0)
        except Exception:
            pass
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        buf.seek(0)
        return buf.read(), "jpg"

    try:
        img = Image.open(io.BytesIO(file_bytes))
        fmt = (img.format or "").lower()
        if fmt == "gif":
            try:
                img.seek(0)
            except Exception:
                pass
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=90)
            buf.seek(0)
            return buf.read(), "jpg"
        if fmt not in ["jpeg", "jpg", "png"]:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=90)
            buf.seek(0)
            return buf.read(), "jpg"
        return file_bytes, ("jpg" if fmt == "jpeg" else fmt)
    except Exception as e:
        raise ValueError(f"normalize_image: unsupported or corrupted image bytes: {e}")


