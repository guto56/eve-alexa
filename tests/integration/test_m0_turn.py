"""Turno completo do M0 com dublês no lugar do hardware de áudio.

Prova o que o M0 promete sem depender de placa de som: a tecla dispara a
captura, o áudio atravessa a fronteira, volta pelo caminho de reprodução e o
TurnTrace registra o que aconteceu.
"""

from __future__ import annotations

import asyncio
import math

import numpy as np

from eve.agent.orchestrator import Orchestrator
from eve.client.voice_client import VoiceClient
from eve.core.audio import AudioFormat, AudioFrame
from eve.core.contracts import PTTEvent
from eve.core.events import TurnState
from eve.transport.inprocess import InProcessTransport

FMT = AudioFormat(sample_rate=16_000, channels=1, frame_ms=20)


def tone(seconds: float, amplitude: float = 0.5, freq: int = 440) -> list[AudioFrame]:
    total = int(FMT.sample_rate * seconds)
    t = np.arange(total) / FMT.sample_rate
    pcm = (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)
    n = FMT.frame_samples
    return [
        AudioFrame(pcm[i : i + n].tobytes(), FMT)
        for i in range(0, total - n + 1, n)
    ]


class FakePTT:
    def __init__(self, script: list[PTTEvent]) -> None:
        self._script = script

    async def start(self) -> None: ...
    async def close(self) -> None: ...

    async def events(self):
        for ev in self._script:
            await asyncio.sleep(0.01)
            yield ev


class FakeSource:
    """Emite os quadros combinados enquanto estiver 'ligado'."""

    def __init__(self, frames: list[AudioFrame]) -> None:
        self._frames = frames
        self._queue: asyncio.Queue = asyncio.Queue()
        self.starts = 0
        self.stops = 0

    @property
    def format(self) -> AudioFormat:
        return FMT

    async def start(self) -> None:
        self.starts += 1
        for f in self._frames:
            self._queue.put_nowait(f)

    async def stop(self) -> None:
        self.stops += 1

    async def close(self) -> None:
        self._queue.put_nowait(None)

    async def frames(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


class FakeSink:
    def __init__(self) -> None:
        self.written: list[AudioFrame] = []
        self.flushes = 0
        self.stops = 0

    async def write(self, frame: AudioFrame) -> None:
        self.written.append(frame)

    async def flush(self) -> None:
        self.flushes += 1

    async def stop(self) -> None:
        self.stops += 1

    async def close(self) -> None: ...


async def run_turn(frames: list[AudioFrame]):
    transport = InProcessTransport()
    source = FakeSource(frames)
    sink = FakeSink()
    ptt = FakePTT([PTTEvent.DOWN, PTTEvent.UP])

    states: list[TurnState] = []
    reports: list[str] = []

    client = VoiceClient(
        transport, ptt, source, sink,
        on_state=states.append,
        on_report=reports.append,
    )
    core = Orchestrator(transport, FMT)

    tasks = [asyncio.create_task(client.run()), asyncio.create_task(core.run())]
    for _ in range(400):                      # até 4 s
        await asyncio.sleep(0.01)
        if reports:
            break
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return sink, states, reports


def test_turno_completo_ecoa_o_audio():
    entrada = tone(1.0)
    sink, states, reports = asyncio.run(run_turn(entrada))

    assert reports, "o turno não produziu TurnTrace"
    assert [f.data for f in sink.written] == [f.data for f in entrada], \
        "o áudio que voltou não é byte-a-byte o que entrou"
    assert sink.flushes == 1, "o cliente precisa esperar o áudio terminar antes de fechar o turno"
    assert sink.stops >= 1, "ptt_down deve cortar o alto-falante antes de abrir o microfone"

    assert TurnState.LISTENING in states
    assert TurnState.SPEAKING in states
    assert states[-1] is TurnState.IDLE

    relatorio = reports[0]
    assert "Turn 1" in relatorio
    assert "Status:    OK" in relatorio
    assert "1.00 s" in relatorio, relatorio


def test_silencio_digital_e_sinalizado():
    n = FMT.frame_samples
    mudo = [AudioFrame(np.zeros(n, dtype=np.int16).tobytes(), FMT) for _ in range(50)]
    _, _, reports = asyncio.run(run_turn(mudo))
    assert "SEM SINAL" in reports[0], reports[0]
    assert "permissão de Microfone" in reports[0]


def test_sinal_fraco_e_sinalizado():
    fraco = tone(1.0, amplitude=10 ** (-50 / 20))     # -50 dBFS
    _, _, reports = asyncio.run(run_turn(fraco))
    assert "sinal muito baixo" in reports[0], reports[0]


def test_sem_audio_nenhum_encerra_o_turno():
    _, states, reports = asyncio.run(run_turn([]))
    assert reports, "um turno sem áudio ainda precisa fechar, senão a EVE trava"
    assert "SEM ÁUDIO" in reports[0]
    assert states[-1] is TurnState.IDLE


class SlowFlushSink(FakeSink):
    """Reprodução que demora, para abrir a janela em que o barge-in acontece."""

    def __init__(self, delay: float = 0.3) -> None:
        super().__init__()
        self._delay = delay

    async def flush(self) -> None:
        await asyncio.sleep(self._delay)
        self.flushes += 1


def test_barge_in_nao_deixa_o_turno_anterior_fechar_o_novo():
    """Apertar a tecla durante a reprodução: o SpeakDone atrasado do turno 1 não
    pode encerrar o turno 2, que acabou de começar."""

    async def cenario():
        transport = InProcessTransport()
        source = FakeSource(tone(0.4))
        sink = SlowFlushSink(delay=0.3)

        class Roteiro:
            async def start(self) -> None: ...
            async def close(self) -> None: ...

            async def events(self):
                yield PTTEvent.DOWN
                await asyncio.sleep(0.05)
                yield PTTEvent.UP          # turno 1 entra em reprodução (lenta)
                await asyncio.sleep(0.05)
                yield PTTEvent.DOWN        # barge-in no meio da reprodução
                await asyncio.sleep(0.05)
                yield PTTEvent.UP          # turno 2
                await asyncio.sleep(5)

        reports: list[str] = []
        client = VoiceClient(transport, Roteiro(), source, sink, on_report=reports.append)
        core = Orchestrator(transport, FMT)
        tasks = [asyncio.create_task(client.run()), asyncio.create_task(core.run())]
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(reports) >= 2:
                break
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return reports

    reports = asyncio.run(cenario())
    assert len(reports) >= 2, f"esperava dois turnos, veio {len(reports)}"
    assert "Turn 1" in reports[0] and "INTERROMPIDO" in reports[0], reports[0]
    assert "Turn 2" in reports[1], reports[1]
