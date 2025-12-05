from __future__ import annotations

import os
import queue
import threading
from datetime import datetime
from typing import Optional

import sounddevice as sd
import soundfile as sf


class Recorder:
    """Microphone recorder that saves audio to WAV using sounddevice."""

    def __init__(self, media_dir: str, *, sample_rate: int = 16000, channels: int = 1):
        self.media_dir = media_dir
        os.makedirs(self.media_dir, exist_ok=True)
        self.sample_rate = sample_rate
        self.channels = channels

        self._queue: queue.Queue = queue.Queue()
        self._stream: Optional[sd.InputStream] = None
        self._file: Optional[sf.SoundFile] = None
        self._recording = False
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time, status):  # noqa: ARG002
        if self._recording:
            self._queue.put(indata.copy())

    def start(self) -> str:
        with self._lock:
            if self._recording:
                raise RuntimeError("Recorder already running.")
            filename = os.path.join(
                self.media_dir, f"clip-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
            )
            self._file = sf.SoundFile(
                filename, mode="w", samplerate=self.sample_rate, channels=self.channels
            )
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._callback,
            )
            self._stream.start()
            self._recording = True
            self._queue = queue.Queue()
            return filename

    def stop(self) -> str:
        with self._lock:
            if not self._recording:
                raise RuntimeError("Recorder is not running.")
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
            while not self._queue.empty():
                data = self._queue.get()
                self._file.write(data)
            filename = self._file.name
            self._file.close()
            return filename

