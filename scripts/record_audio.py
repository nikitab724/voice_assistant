# scripts/record_audio.py
from __future__ import annotations

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from voice_transcription import Recorder

MEDIA_DIR = os.path.join(ROOT_DIR, "media")


def main():
    recorder = Recorder(MEDIA_DIR)
    print("Press Enter to start recording; press Enter again to stop.")
    input("Ready? Press Enter to start…")
    recorder.start()
    input("Recording... press Enter to stop.")
    recorder.stop()


if __name__ == "__main__":
    main()