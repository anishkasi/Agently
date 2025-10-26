from openai import OpenAI
from core import settings


def describe_image(image_url: str) -> str:
    """Return a concise description of an image using a vision-capable model."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    system_msg = (
        "You are an assistant that describes images briefly and factually. Make sure to be descriptive, detailed and concise."
    )
    user_content = [
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    resp = client.chat.completions.create(
        model=settings.VISION_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ]
    )
    return resp.choices[0].message.content.strip()



