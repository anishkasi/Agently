import os
import uuid
from supabase import create_client, Client
import aiohttp
from core import settings

from adapter.utils.image import normalize_image


SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_KEY = settings.SUPABASE_KEY
BUCKET_NAME = settings.SUPABASE_BUCKET

_supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


async def upload_to_supabase(file_url: str, file_type: str, group_id: int, user_id: int) -> str:
    """Upload a file to Supabase storage and return the public URL.
    Args:
        file_url: The URL of the file to upload
        file_type: The type of the file (image, video, audio, etc.)
        group_id: The ID of the group the file belongs to
        user_id: The ID of the user the file belongs to

    Returns:
        The public URL of the uploaded file
    """
    if _supabase is None:
        raise RuntimeError("Supabase client not configured")
    filename = f"{uuid.uuid4()}.{file_type}"
    storage_path = f"{file_type}/{group_id}/{user_id}/{filename}"
    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch file: {resp.status}")
            file_bytes = await resp.read()
            if file_type in {"image", "GIF"}:
                file_bytes, _ = await normalize_image(file_bytes)
    _supabase.storage.from_(BUCKET_NAME).upload(storage_path, file_bytes)
    public_url = _supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)
    return public_url


