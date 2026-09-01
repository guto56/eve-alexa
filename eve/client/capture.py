"""Captura de microfone via PortAudio/CoreAudio.

O stream é construído uma vez e depois só ligado e desligado por turno: abrir o
dispositivo custa dezenas de milissegundos e cortaria a primeira sílaba a cada
vez que você apertasse a tecla. Ligar um stream já aberto é quase instantâneo — e
o indicador laranja do macOS acende apenas enquanto ele está rodando.

O que sai daqui é sempre 16 kHz mono PCM16, independente do que o dispositivo
entregue. Negociação e reamostragem são problema do cliente, nunca do EVE Core.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import numpy as np

from eve.client.devices import DeviceInfo
from eve.client.resample import Resampler
from eve.core.audio import AudioFormat, AudioFrame

_SENTINEL = object()


class MicrophoneSource:
    """Implementa o Protocol AudioSource."""

    def __init__(self, device: DeviceInfo, fmt: AudioFormat, *, queue_max: int = 512) -> None:
        self._device = device
        self._format = fmt
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream = None
        self._resampler: Resampler | None = None
        self._pending = bytearray()
        self._capture_rate = fmt.sample_rate
        self._capture_channels = 1
        self._running = False
        self._dropped = 0
        self.last_open_ms: float | None = None

    # ---------- descrição ----------

    @property
    def format(self) -> AudioFormat:
        return self._format

    @property
    def device(self) -> DeviceInfo:
        return self._device

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    def describe(self) -> str:
        if self._capture_rate == self._format.sample_rate:
            taxa = f"{self._format.sample_rate} Hz"
        else:
            taxa = f"{self._capture_rate} Hz nativo → {self._format.sample_rate} Hz"
        canais = "mono" if self._capture_channels == 1 else f"{self._capture_channels} ch → mono"
        return f"{self._device.name}  ({canais} · {taxa})"

    # ---------- negociação ----------

    def _negotiate(self) -> None:
        """Descobre a melhor combinação que o dispositivo aceita de fato."""
        import sounddevice as sd

        rate = self._format.sample_rate
        channels = 1
        try:
            sd.check_input_settings(
                device=self._device.index, channels=1, dtype="int16", samplerate=rate
            )
        except Exception:
            # O dispositivo recusou 16 kHz mono. Cai para o nativo dele e converte aqui.
            rate = self._device.default_samplerate
            channels = 1
            try:
                sd.check_input_settings(
                    device=self._device.index, channels=1, dtype="int16", samplerate=rate
                )
            except Exception:
                channels = max(1, self._device.max_input)
                sd.check_input_settings(
                    device=self._device.index, channels=channels, dtype="int16", samplerate=rate
                )

        self._capture_rate = rate
        self._capture_channels = channels
        self._resampler = Resampler(rate, self._format.sample_rate)

    def prepare(self) -> None:
        """Negocia taxa e canais sem começar a capturar.

        Existe para a CLI poder mostrar a taxa real na linha "Input:" antes de o
        primeiro turno acontecer, sem alcançar dentro da classe.
        """
        if self._resampler is None:
            self._negotiate()

    def _open(self) -> None:
        import sounddevice as sd

        self.prepare()
        blocksize = self._capture_rate * self._format.frame_ms // 1000
        self._stream = sd.InputStream(
            device=self._device.index,
            samplerate=self._capture_rate,
            channels=self._capture_channels,
            dtype="int16",
            blocksize=blocksize,
            callback=self._on_audio,
        )

    # ---------- callback (thread da PortAudio) ----------

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if not self._running or self._loop is None:
            return

        pcm = np.array(indata, dtype=np.int16, copy=True)
        if pcm.ndim == 2:
            pcm = pcm.mean(axis=1).astype(np.int16) if pcm.shape[1] > 1 else pcm[:, 0]

        if self._resampler is not None and self._resampler.active:
            pcm = self._resampler.process(pcm)
        if pcm.size == 0:
            return

        self._pending.extend(pcm.tobytes())
        n = self._format.frame_bytes
        while len(self._pending) >= n:
            chunk = bytes(self._pending[:n])
            del self._pending[:n]
            frame = AudioFrame(data=chunk, format=self._format)
            self._loop.call_soon_threadsafe(self._offer, frame)

    def _offer(self, frame: AudioFrame) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Descartar é melhor que travar o callback de áudio, mas é sintoma
            # de que o consumidor não está acompanhando — por isso é contado.
            self._dropped += 1

    # ---------- ciclo de vida ----------

    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        began = time.perf_counter()
        if self._stream is None:
            self._open()
        self._pending.clear()
        self._resampler = Resampler(self._capture_rate, self._format.sample_rate)
        self._running = True
        self._stream.start()
        self.last_open_ms = (time.perf_counter() - began) * 1000.0

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._stream is not None:
            self._stream.stop()

    async def close(self) -> None:
        await self.stop()
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, _SENTINEL)

    async def frames(self) -> AsyncIterator[AudioFrame]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            yield item
