"""Reprodução pelo alto-falante via PortAudio/CoreAudio.

Streaming de verdade, não `sd.play()` de um buffer pronto: no M1 o áudio chega
do TTS em pedaços enquanto o LLM ainda está gerando, e `stop()` precisa cortar o
som no meio da frase para o barge-in funcionar.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque

import numpy as np

from eve.client.devices import DeviceInfo
from eve.client.resample import Resampler
from eve.core.audio import AudioFormat, AudioFrame


class SpeakerSink:
    """Implementa o Protocol AudioSink."""

    def __init__(self, device: DeviceInfo) -> None:
        self._device = device
        self._stream = None
        self._lock = threading.Lock()
        self._buffer = deque()          # de np.ndarray int16 mono
        self._pending = np.zeros(0, dtype=np.int16)
        self._stream_rate: int | None = None
        self._resampler: Resampler | None = None
        self._underflows = 0

    @property
    def device(self) -> DeviceInfo:
        return self._device

    def describe(self) -> str:
        taxa = f"{self._stream_rate} Hz" if self._stream_rate else f"{self._device.default_samplerate} Hz"
        return f"{self._device.name}  (mono · {taxa})"

    # ---------- abertura ----------

    def _ensure_stream(self, fmt: AudioFormat) -> None:
        import sounddevice as sd

        if self._stream is not None and self._stream_rate is not None:
            return

        rate = fmt.sample_rate
        try:
            sd.check_output_settings(
                device=self._device.index, channels=1, dtype="int16", samplerate=rate
            )
        except Exception:
            rate = self._device.default_samplerate

        self._stream_rate = rate
        self._resampler = Resampler(fmt.sample_rate, rate)
        self._stream = sd.OutputStream(
            device=self._device.index,
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=rate * fmt.frame_ms // 1000,
            callback=self._on_need_audio,
        )
        self._stream.start()

    # ---------- callback (thread da PortAudio) ----------

    def _on_need_audio(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        need = frames
        out = np.zeros(need, dtype=np.int16)
        filled = 0
        with self._lock:
            while filled < need and (self._pending.size or self._buffer):
                if self._pending.size == 0:
                    self._pending = self._buffer.popleft()
                take = min(need - filled, self._pending.size)
                out[filled : filled + take] = self._pending[:take]
                self._pending = self._pending[take:]
                filled += take
        if filled < need:
            self._underflows += 1
        outdata[:] = out.reshape(-1, 1)

    @property
    def _queued_samples(self) -> int:
        with self._lock:
            return self._pending.size + sum(a.size for a in self._buffer)

    # ---------- API ----------

    async def write(self, frame: AudioFrame) -> None:
        self._ensure_stream(frame.format)
        pcm = np.frombuffer(frame.data, dtype=np.int16)
        if self._resampler is not None and self._resampler.active:
            pcm = self._resampler.process(pcm)
        if pcm.size:
            with self._lock:
                self._buffer.append(pcm)

    async def flush(self) -> None:
        """Espera o áudio realmente sair pelo alto-falante."""
        if self._stream is None:
            return
        while self._queued_samples > 0:
            await asyncio.sleep(0.005)
        # O que já foi entregue à PortAudio ainda leva a latência de saída
        # para virar som. Sem esta espera o TurnTrace mediria menos do que
        # você de fato ouviu.
        await asyncio.sleep(float(getattr(self._stream, "latency", 0.0) or 0.0))

    async def stop(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._pending = np.zeros(0, dtype=np.int16)

    async def close(self) -> None:
        await self.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
