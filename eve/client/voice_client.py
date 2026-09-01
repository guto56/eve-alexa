"""Voice Client.

Dono do microfone, do alto-falante e da tecla. Não sabe o que é STT, LLM ou TTS
— só empurra áudio e eventos pela fronteira e toca o que vem de volta. É esta
peça que, na Fase 3, sai do Mac e vira o dispositivo físico.
"""

from __future__ import annotations

import asyncio

from eve.core.contracts import AudioSink, AudioSource, PTTEvent, PushToTalk, Transport
from eve.core.events import (
    AudioChunk,
    AudioOut,
    Notice,
    PttDown,
    PttUp,
    Shutdown,
    SpeakDone,
    SpeakEnd,
    State,
    TurnComplete,
)


class VoiceClient:
    def __init__(
        self,
        transport: Transport,
        ptt: PushToTalk,
        source: AudioSource,
        sink: AudioSink,
        *,
        on_state=None,
        on_report=None,
        on_notice=None,
    ) -> None:
        self._transport = transport
        self._ptt = ptt
        self._source = source
        self._sink = sink
        self._on_state = on_state or (lambda _: None)
        self._on_report = on_report or (lambda text: print(text))
        self._on_notice = on_notice or (lambda text: print(text))
        self._capturing = False

    async def run(self) -> None:
        await self._ptt.start()
        await asyncio.gather(
            self._pump_ptt(),
            self._pump_microphone(),
            self._pump_core(),
        )

    # ---------- Voice Client -> EVE Core ----------

    async def _pump_ptt(self) -> None:
        async for event in self._ptt.events():
            if event is PTTEvent.DOWN:
                # Cortar o alto-falante antes de abrir o microfone é o que impede
                # a EVE de gravar a própria voz — o barge-in do M0.
                await self._sink.stop()
                self._capturing = True
                await self._source.start()
                await self._transport.to_core(
                    PttDown(open_ms=getattr(self._source, "last_open_ms", None))
                )
            else:
                self._capturing = False
                await self._source.stop()
                await self._transport.to_core(PttUp())

    async def _pump_microphone(self) -> None:
        async for frame in self._source.frames():
            if self._capturing:
                await self._transport.to_core(AudioChunk(frame))

    # ---------- EVE Core -> Voice Client ----------

    async def _pump_core(self) -> None:
        async for event in self._transport.core_events():
            if isinstance(event, AudioOut):
                await self._sink.write(event.frame)
            elif isinstance(event, SpeakEnd):
                await self._sink.flush()
                await self._transport.to_core(SpeakDone())
            elif isinstance(event, State):
                self._on_state(event.value)
            elif isinstance(event, TurnComplete):
                self._on_report(event.report)
            elif isinstance(event, Notice):
                self._on_notice(event.text)

    async def close(self) -> None:
        await self._transport.to_core(Shutdown())
        await self._ptt.close()
        await self._source.close()
        await self._sink.close()
        await self._transport.close()
