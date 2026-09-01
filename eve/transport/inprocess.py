"""Transporte em memória.

Os dois lados rodam no mesmo processo nesta fase, mas conversam exclusivamente
pelos eventos definidos em eve.core.events. Trocar esta classe por um par
cliente/servidor WebSocket não deve exigir mudança em nenhum outro módulo — é
esse o teste de que a fronteira do dispositivo é real.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from eve.core.events import ClientEvent, CoreEvent

_CLOSED = object()


class InProcessTransport:
    """Implementa o Protocol Transport."""

    def __init__(self) -> None:
        self._to_core: asyncio.Queue = asyncio.Queue()
        self._to_client: asyncio.Queue = asyncio.Queue()

    async def to_core(self, event: ClientEvent) -> None:
        await self._to_core.put(event)

    async def to_client(self, event: CoreEvent) -> None:
        await self._to_client.put(event)

    async def client_events(self) -> AsyncIterator[ClientEvent]:
        while True:
            item = await self._to_core.get()
            if item is _CLOSED:
                return
            yield item

    async def core_events(self) -> AsyncIterator[CoreEvent]:
        while True:
            item = await self._to_client.get()
            if item is _CLOSED:
                return
            yield item

    async def close(self) -> None:
        await self._to_core.put(_CLOSED)
        await self._to_client.put(_CLOSED)
