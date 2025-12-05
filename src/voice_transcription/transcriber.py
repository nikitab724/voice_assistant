from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from openai import OpenAI

from app_config import get_openai_settings


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    settings = get_openai_settings()
    return OpenAI(api_key=settings.api_key)


def transcribe_file(media_path: str, model_name: str = "turbo") -> str:
    media_path = os.path.abspath(media_path)
    if not os.path.exists(media_path):
        raise FileNotFoundError(media_path)
    client = _get_client()
    with open(media_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1" if model_name == "turbo" else model_name,
            file=audio_file,
            response_format="text",
        )
    return response.strip()


def save_transcript(media_path: str, text: str, output_dir: Optional[str] = None) -> str:
    output_dir = output_dir or os.path.join(os.path.dirname(media_path), "transcripts")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(
        output_dir, f"{os.path.splitext(os.path.basename(media_path))[0]}.txt"
    )
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return output_path

