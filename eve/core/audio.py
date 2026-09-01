"""Formato e quadros de áudio.

PCM16 little-endian é o único formato que trafega entre as camadas. Cada quadro
carrega o próprio formato para que o destino saiba como reproduzi-lo sem que
ninguém precise combinar taxa de amostragem por fora.
"""

from __future__ import annotations

from dataclasses import dataclass

PCM16_WIDTH = 2


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16_000
    channels: int = 1
    frame_ms: int = 20

    @property
    def frame_samples(self) -> int:
        return self.sample_rate * self.frame_ms // 1000

    @property
    def frame_bytes(self) -> int:
        return self.frame_samples * self.channels * PCM16_WIDTH

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * PCM16_WIDTH

    def duration_of(self, nbytes: int) -> float:
        return nbytes / self.bytes_per_second

    def describe(self) -> str:
        canais = "mono" if self.channels == 1 else f"{self.channels} canais"
        return f"{self.sample_rate} Hz · {canais} · PCM16"


@dataclass(frozen=True)
class AudioFrame:
    data: bytes
    format: AudioFormat

    @property
    def duration_s(self) -> float:
        return self.format.duration_of(len(self.data))
