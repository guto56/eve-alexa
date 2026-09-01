"""TurnTrace — a instrumentação do turno.

Existe desde o M0 por decisão de projeto: o objetivo da Fase 1 é avaliar a
experiência, a experiência é dominada por latência, e latência que não se mede
se otimiza por palpite.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from eve.core.audio import AudioFormat


def _dbfs(value: float) -> float:
    if value <= 0:
        return -math.inf
    return 20.0 * math.log10(min(value, 1.0))


def _fmt_db(value: float) -> str:
    return "-inf" if math.isinf(value) else f"{value:.1f}"


@dataclass
class TurnTrace:
    turn: int
    marks: dict[str, float] = field(default_factory=dict)
    audio_bytes: int = 0
    audio_format: AudioFormat | None = None
    peak: float = 0.0
    rms: float = 0.0
    stream_open_ms: float | None = None
    status: str = "OK"
    notes: list[str] = field(default_factory=list)

    def mark(self, name: str) -> None:
        self.marks[name] = time.perf_counter()

    def span_ms(self, start: str, end: str) -> float | None:
        if start not in self.marks or end not in self.marks:
            return None
        return (self.marks[end] - self.marks[start]) * 1000.0

    @property
    def audio_seconds(self) -> float:
        if not self.audio_format:
            return 0.0
        return self.audio_format.duration_of(self.audio_bytes)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def render(self) -> str:
        capture = self.span_ms("ptt_down", "ptt_up")
        playback = self.span_ms("speak_start", "speak_done")
        total = self.span_ms("ptt_down", "speak_done")

        def ms(value: float | None) -> str:
            return "—" if value is None else f"{value:.0f} ms"

        fmt = self.audio_format.describe() if self.audio_format else "—"
        abertura = (
            f"   (abertura do stream: {self.stream_open_ms:.0f} ms)"
            if self.stream_open_ms is not None
            else ""
        )

        linhas = [
            f"\n── Turn {self.turn} " + "─" * 46,
            f"  Audio:     {self.audio_seconds:.2f} s      ({self.audio_bytes} bytes · {fmt})",
            f"  Capture:   {ms(capture)}{abertura}",
            f"  Playback:  {ms(playback)}",
            f"  Level:     pico {_fmt_db(_dbfs(self.peak))} dBFS · rms {_fmt_db(_dbfs(self.rms))} dBFS",
            f"  Total:     {ms(total)}",
            f"  Status:    {self.status}",
        ]
        for nota in self.notes:
            linhas.append(f"             ↳ {nota}")
        return "\n".join(linhas)
