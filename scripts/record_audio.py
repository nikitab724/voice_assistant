# scripts/record_audio.py
from __future__ import annotations

import argparse
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from voice_transcription import Recorder, list_input_devices

MEDIA_DIR = os.path.join(ROOT_DIR, "media")


def main():
    parser = argparse.ArgumentParser(description="Record audio clips with sounddevice.")
    parser.add_argument(
        "--device",
        help="Input device index or name. Defaults to VOICE_INPUT_DEVICE env or system default.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available input devices and exit.",
    )
    args = parser.parse_args()

    if args.list_devices:
        print("Available input devices:")
        for line in list_input_devices():
            print(" -", line)
        return

    recorder = Recorder(MEDIA_DIR, device=args.device)
    print("Press Enter to start recording; press Enter again to stop.")
    input("Ready? Press Enter to start…")
    recorder.start()
    if recorder.last_device_info:
        print(f"Recording with: {recorder.last_device_info}")
    input("Recording... press Enter to stop.")
    filename = recorder.stop()
    print(f"Saved to {filename}")
    if recorder.last_warning:
        print("Warning:", recorder.last_warning)
        print(
            "Tips: grant microphone access to your terminal app and choose the correct device using --device."
        )


if __name__ == "__main__":
    main()