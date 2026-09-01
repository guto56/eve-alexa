"""EVE Core — orquestrador.

No M0 o turno é um eco: o que entrou pelo microfone volta pelo alto-falante.
Nenhum STT, LLM ou TTS existe ainda. O que já é definitivo aqui é a forma: o
orquestrador é a única peça que conhece o estado do turno, ele só fala com o
Voice Client por eventos, e todo turno produz um TurnTrace.
"""

from __future__ import annotations

import math

import numpy as np

from eve.core.audio import AudioFormat, AudioFrame
from eve.core.contracts import Transport
from eve.core.events import (
    AudioChunk,
    AudioOut,
    PttDown,
    PttUp,
    Shutdown,
    SpeakDone,
    SpeakEnd,
    State,
    TurnComplete,
    TurnState,
)
from eve.core.trace import TurnTrace

# Abaixo disto a gravação existe, mas está fraca demais para um STT trabalhar bem.
NIVEL_BAIXO_DBFS = -45.0
DURACAO_MINIMA_S = 0.2


class Orchestrator:
    def __init__(self, transport: Transport, fmt: AudioFormat) -> None:
        self._transport = transport
        self._format = fmt
        self._turn = 0
        self._trace: TurnTrace | None = None
        self._buffer: list[AudioFrame] = []
        # De qual turno estamos esperando o SpeakDone. Sem isso, apertar a tecla
        # durante a reprodução faria o SpeakDone do turno anterior fechar o
        # turno novo, que mal tinha começado.
        self._awaiting_speak: int | None = None

    async def run(self) -> None:
        async for event in self._transport.client_events():
            if isinstance(event, PttDown):
                await self._begin_turn(event.open_ms)
            elif isinstance(event, AudioChunk):
                if self._trace is not None:
                    self._buffer.append(event.frame)
            elif isinstance(event, PttUp):
                await self._end_capture()
            elif isinstance(event, SpeakDone):
                await self._finish_turn()
            elif isinstance(event, Shutdown):
                return

    # ---------- turno ----------

    async def _begin_turn(self, open_ms: float | None = None) -> None:
        if self._trace is not None:
            # Barge-in: a tecla foi pressionada com um turno ainda no ar.
            await self._abandon_turn()
        self._turn += 1
        self._trace = TurnTrace(turn=self._turn, audio_format=self._format)
        self._trace.stream_open_ms = open_ms
        self._trace.mark("ptt_down")
        self._buffer = []
        await self._transport.to_client(State(TurnState.LISTENING))

    async def _end_capture(self) -> None:
        trace = self._trace
        if trace is None:
            return
        trace.mark("ptt_up")

        pcm = b"".join(f.data for f in self._buffer)
        trace.audio_bytes = len(pcm)
        self._measure(trace, pcm)

        await self._transport.to_client(State(TurnState.SPEAKING))
        trace.mark("speak_start")

        if not pcm:
            # Sem áudio não há o que reproduzir: encerra o turno imediatamente,
            # senão ficaríamos esperando um SpeakDone que nunca viria.
            await self._transport.to_client(SpeakEnd())
            trace.mark("speak_done")
            await self._finish_turn()
            return

        for frame in self._buffer:
            await self._transport.to_client(AudioOut(frame))
        await self._transport.to_client(SpeakEnd())
        self._awaiting_speak = trace.turn

    async def _abandon_turn(self) -> None:
        """Fecha um turno interrompido para que ele apareça no terminal em vez
        de sumir, e invalida o SpeakDone que ainda está a caminho."""
        trace = self._trace
        self._trace = None
        self._buffer = []
        self._awaiting_speak = None
        if trace is None:
            return
        trace.mark("speak_done")
        trace.status = "INTERROMPIDO"
        trace.note("Você apertou a tecla antes de a reprodução terminar.")
        await self._transport.to_client(TurnComplete(report=trace.render()))

    async def _finish_turn(self) -> None:
        trace = self._trace
        if trace is None:
            return
        if self._awaiting_speak is not None and self._awaiting_speak != trace.turn:
            return  # SpeakDone atrasado, de um turno já abandonado
        self._awaiting_speak = None
        if "speak_done" not in trace.marks:
            trace.mark("speak_done")
        await self._transport.to_client(State(TurnState.IDLE))
        await self._transport.to_client(TurnComplete(report=trace.render()))
        self._trace = None
        self._buffer = []

    # ---------- diagnóstico da captura ----------

    def _measure(self, trace: TurnTrace, pcm: bytes) -> None:
        if not pcm:
            trace.status = "SEM ÁUDIO"
            trace.note("Nenhum quadro chegou. A tecla foi solta antes de a captura começar?")
            return

        amostras = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        trace.peak = float(np.abs(amostras).max())
        trace.rms = float(np.sqrt(np.mean(amostras**2)))

        if trace.peak == 0.0:
            trace.status = "SEM SINAL"
            trace.note(
                "O microfone entregou silêncio digital absoluto — sinal clássico de "
                "permissão de Microfone negada, ou de entrada mutada no macOS."
            )
            return

        pico_db = 20.0 * math.log10(trace.peak)
        if trace.audio_seconds < DURACAO_MINIMA_S:
            trace.status = "OK (gravação muito curta)"
            trace.note("Segure a tecla enquanto fala; soltar cedo demais corta a frase.")
        elif pico_db < NIVEL_BAIXO_DBFS:
            trace.status = "OK (sinal muito baixo)"
            trace.note(
                "Fale mais perto ou aumente o ganho da entrada em Ajustes do Sistema › Som. "
                "Neste nível o STT do M1 vai errar muito."
            )
        else:
            trace.status = "OK"

