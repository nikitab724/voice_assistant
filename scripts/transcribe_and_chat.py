from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

# Add src/ for imports
ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voice_transcription import transcribe_file


def transcribe_with_whisper(media_path: str) -> str:
    return transcribe_file(media_path)


def send_message(message: str, session_id: str = "voice") -> dict:
    resp = requests.post(
        "http://localhost:5050/api/chat",
        json={"session_id": session_id, "message": message},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Record, transcribe, and chat via Flask backend.")
    parser.add_argument("--file", required=True, help="Path to audio file to transcribe.")
    parser.add_argument("--session", default="voice", help="Session ID for the chat agent.")
    args = parser.parse_args()

    media_path = args.file
    if not os.path.exists(media_path):
        raise FileNotFoundError(media_path)

    transcript = transcribe_with_whisper(media_path)
    print(f"[transcript] {transcript}")
    response = send_message(transcript, session_id=args.session)
    print("[assistant]", response.get("text"))


if __name__ == "__main__":
    main()

