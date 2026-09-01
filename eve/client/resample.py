"""Reamostragem PCM16, mínima e com estado.

Só entra em ação quando o dispositivo recusa 16 kHz e precisamos capturar na
taxa nativa dele. Mantém a cauda do bloco anterior para que a filtragem não
crie um estalo a cada 20 ms — o que faria você achar que o microfone está com
defeito quando o problema seria este código.
"""

from __future__ import annotations

import math

import numpy as np


class Resampler:
    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._passthrough = src_rate == dst_rate
        self._factor = src_rate // dst_rate if src_rate % dst_rate == 0 else 0
        self._tail = np.zeros(0, dtype=np.float32)
        self._phase = 0.0

    @property
    def active(self) -> bool:
        return not self._passthrough

    def process(self, pcm: np.ndarray) -> np.ndarray:
        if self._passthrough:
            return pcm

        samples = np.concatenate([self._tail, pcm.astype(np.float32)])

        if self._factor >= 2:
            # Razão inteira: média móvel como passa-baixas, depois decimação.
            n = self._factor
            usable = (len(samples) // n) * n
            if usable == 0:
                self._tail = samples
                return np.zeros(0, dtype=np.int16)
            self._tail = samples[usable:]
            blocks = samples[:usable].reshape(-1, n)
            out = blocks.mean(axis=1)
        else:
            # Razão não inteira: interpolação linear com fase contínua.
            step = self.src_rate / self.dst_rate
            count = int(math.floor((len(samples) - 1 - self._phase) / step)) + 1
            if count <= 0:
                self._tail = samples
                return np.zeros(0, dtype=np.int16)
            idx = self._phase + step * np.arange(count)
            out = np.interp(idx, np.arange(len(samples)), samples)
            # A próxima saída fica em idx[-1] + step; guardar idx[-1] emitiria
            # a mesma posição duas vezes e faria a duração derivar.
            next_pos = idx[-1] + step
            consumed = int(math.floor(next_pos))
            self._phase = next_pos - consumed
            self._tail = samples[consumed:]

        return np.clip(out, -32768, 32767).astype(np.int16)
