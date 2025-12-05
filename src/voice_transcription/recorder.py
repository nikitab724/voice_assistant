from __future__ import annotations

import os
import queue
import threading
from datetime import datetime
from typing import Optional, Union

import sounddevice as sd
import soundfile as sf


DeviceSpecifier = Union[int, str, None]


class Recorder:
    """Microphone recorder that saves audio to WAV using sounddevice."""

    def __init__(
        self,
        media_dir: str,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        device: DeviceSpecifier = None,
    ):
        self.media_dir = media_dir
        os.makedirs(self.media_dir, exist_ok=True)
        self.sample_rate = sample_rate
        self.channels = channels
        self._configured_device = self._coerce_device(device)
        if self._configured_device is None:
            env_device = os.getenv("VOICE_INPUT_DEVICE")
            self._configured_device = self._coerce_device(env_device)

        self._queue: queue.Queue = queue.Queue()
        self._stream: Optional[sd.InputStream] = None
        self._file: Optional[sf.SoundFile] = None
        self._recording = False
        self._lock = threading.Lock()
        self._frames_captured = 0
        self._max_level = 0.0

        self.last_device_info: Optional[str] = None
        self.last_warning: Optional[str] = None

    def _callback(self, indata, frames, time, status):  # noqa: ARG002
        if self._recording:
            self._queue.put(indata.copy())
            self._frames_captured += frames
            try:
                peak = float(abs(indata).max())
            except Exception:
                peak = 0.0
            self._max_level = max(self._max_level, peak)

    @staticmethod
    def _coerce_device(value: DeviceSpecifier) -> DeviceSpecifier:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            try:
                return int(value)
            except ValueError:
                return value
        return value

    def _resolve_device(self) -> DeviceSpecifier:
        if self._configured_device is not None:
            return self._configured_device
        default = sd.default.device
        if isinstance(default, (list, tuple)) and default:
            return default[0]
        return None

    def _describe_device(self, device: DeviceSpecifier) -> str:
        try:
            info = sd.query_devices(device, "input")
        except Exception:
            if device is None or (isinstance(device, int) and device < 0):
                return "default input device"
            return str(device)
        name = info.get("name", "unknown device")
        channels = info.get("max_input_channels")
        idx = info.get("index", device)
        if channels:
            return f"{name} (index {idx}, inputs {channels})"
        return f"{name} (index {idx})"

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
            device = self._resolve_device()
            self.last_device_info = self._describe_device(device)
            self.last_warning = None
            self._frames_captured = 0
            self._max_level = 0.0
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._callback,
                device=device,
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
            self._maybe_set_warning()
            return filename

    def _maybe_set_warning(self) -> None:
        if not self._frames_captured:
            self.last_warning = (
                "No audio frames captured. Check microphone permissions for Terminal/Cursor."
            )
            return
        if self._max_level < 1e-4:
            self.last_warning = (
                "Audio level was nearly zero. Ensure the correct microphone/device is selected."
            )
        else:
            self.last_warning = None


def list_input_devices() -> list[str]:
    """Return human-readable descriptions of available input devices."""
    descriptions: list[str] = []
    try:
        devices = sd.query_devices()
    except Exception as exc:  # pragma: no cover - platform dependent
        return [f"Error listing devices: {exc}"]

    for idx, dev in enumerate(devices):
        inputs = dev.get("max_input_channels", 0)
        if inputs:
            descriptions.append(f"{idx}: {dev.get('name', 'Unknown')} (inputs {inputs})")
    if not descriptions:
        descriptions.append("No input devices reported by sounddevice.")
    return descriptions

