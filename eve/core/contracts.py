"""Contratos entre as camadas.

Protocols, não classes-base: uma implementação só precisa ter os métodos, sem
herdar nada. Tudo é assíncrono e, onde faz sentido, streaming — porque latência
embutida numa assinatura não sai depois com otimização.
"""

from __future__ import annotations

from enum import Enum
from typing import AsyncIterator, Protocol, runtime_checkable

from eve.core.audio import AudioFormat, AudioFrame
from eve.core.events import ClientEvent, CoreEvent


class PTTEvent(str, Enum):
    DOWN = "DOWN"
    UP = "UP"


@runtime_checkable
class AudioSource(Protocol):
    """Microfone. Emite quadros só entre start() e stop()."""

    @property
    def format(self) -> AudioFormat: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def close(self) -> None: ...

    def frames(self) -> AsyncIterator[AudioFrame]: ...


@runtime_checkable
class AudioSink(Protocol):
    """Alto-falante."""

    async def write(self, frame: AudioFrame) -> None: ...

    async def flush(self) -> None:
        """Espera o buffer esvaziar (o áudio realmente terminar de tocar)."""

    async def stop(self) -> None:
        """Descarta o buffer imediatamente — é isso que o barge-in usa."""

    async def close(self) -> None: ...


@runtime_checkable
class PushToTalk(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    def events(self) -> AsyncIterator[PTTEvent]: ...


@runtime_checkable
class Transport(Protocol):
    """A fronteira. Hoje filas em memória; amanhã um WebSocket."""

    async def to_core(self, event: ClientEvent) -> None: ...

    async def to_client(self, event: CoreEvent) -> None: ...

    def client_events(self) -> AsyncIterator[ClientEvent]: ...

    def core_events(self) -> AsyncIterator[CoreEvent]: ...

    async def close(self) -> None: ...
