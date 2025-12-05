from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext
from typing import Optional

import requests

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from voice_transcription import Recorder, transcribe_file

MEDIA_DIR = os.path.join(ROOT_DIR, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

CHAT_ENDPOINT = "http://localhost:5050/api/chat"
SESSION_ID = "voice-ui"


def transcribe_with_whisper(media_path: str) -> str:
    return transcribe_file(media_path)


def send_message(message: str) -> str:
    resp = requests.post(
        CHAT_ENDPOINT,
        json={"session_id": SESSION_ID, "message": message},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("text") or ""


class VoiceUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Voice Assistant UI")
        self.geometry("520x520")

        self.recorder = Recorder(MEDIA_DIR)
        self.recording = False
        self.current_file: Optional[str] = None

        self.text_area = scrolledtext.ScrolledText(self, wrap=tk.WORD, height=15)
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.entry = tk.Entry(self)
        self.entry.pack(fill=tk.X, padx=10, pady=5)
        self.entry.bind("<Return>", self.on_send_text)

        button_frame = tk.Frame(self)
        button_frame.pack(fill=tk.X, padx=10, pady=10)

        self.send_button = tk.Button(button_frame, text="Send Text", command=self.on_send_text)
        self.send_button.pack(side=tk.LEFT, expand=True, padx=5)

        self.record_button = tk.Button(button_frame, text="Record Voice", command=self.on_toggle_record)
        self.record_button.pack(side=tk.LEFT, expand=True, padx=5)

    def append_message(self, role: str, text: str):
        self.text_area.insert(tk.END, f"{role}: {text}\n")
        self.text_area.see(tk.END)

    def on_send_text(self, event=None):
        message = self.entry.get().strip()
        if not message:
            return
        self.entry.delete(0, tk.END)
        self.append_message("You", message)
        threading.Thread(target=self._send_text_async, args=(message,), daemon=True).start()

    def _send_text_async(self, message: str):
        try:
            response = send_message(message)
            self.after(0, lambda resp=response: self.append_message("Assistant", resp))
        except Exception as exc:
            self.after(0, lambda err=exc: self.append_message("Error", str(err)))

    def on_toggle_record(self):
        if not self.recording:
            try:
                self.current_file = self.recorder.start()
                self.recording = True
                self.record_button.config(text="Stop Recording")
                self.append_message("System", "Recording started...")
                if self.recorder.last_device_info:
                    self.append_message(
                        "System", f"Input device: {self.recorder.last_device_info}"
                    )
            except Exception as exc:
                self.append_message("Error", str(exc))
        else:
            try:
                filename = self.recorder.stop()
                self.recording = False
                self.record_button.config(text="Record Voice")
                self.append_message("System", f"Recording saved to {filename}, transcribing...")
                if self.recorder.last_warning:
                    self.append_message("System", f"Warning: {self.recorder.last_warning}")
                threading.Thread(target=self._transcribe_and_send, args=(filename,), daemon=True).start()
            except Exception as exc:
                self.append_message("Error", str(exc))

    def _transcribe_and_send(self, filename: str):
        try:
            transcript = transcribe_with_whisper(filename)
            self.after(0, lambda text=transcript: self.append_message("You (voice)", text))
            response = send_message(transcript)
            self.after(0, lambda resp=response: self.append_message("Assistant", resp))
        except Exception as exc:
            self.after(0, lambda err=exc: self.append_message("Error", str(err)))


if __name__ == "__main__":
    app = VoiceUI()
    app.mainloop()

