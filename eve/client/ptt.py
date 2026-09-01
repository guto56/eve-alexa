"""Push-to-talk com atalho global (pynput).

No macOS o listener do pynput usa um event tap do Quartz, que exige a permissão
de Monitoramento de Entrada. Sem ela o listener sobe normalmente e nunca recebe
nada — falha silenciosa. Por isso este módulo conta *qualquer* tecla vista, e não
só o atalho: se nada chegou depois de alguns segundos, o problema é permissão,
não configuração.

O atalho não é suprimido de propósito. Suprimir teclas no macOS exige um tap
mais invasivo e pode travar o teclado inteiro se o processo morrer no momento
errado — risco que não se justifica no M0.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from eve.core.contracts import PTTEvent

_SENTINEL = object()

_MODIFIERS = {"ctrl", "cmd", "alt", "shift"}
_ALIASES = {
    "control": "ctrl",
    "command": "cmd",
    "meta": "cmd",
    "super": "cmd",
    "option": "alt",
    "opt": "alt",
}
_MAC_VK = {49: "space"}          # ctrl+space chega como caractere de controle


def normalize(name: str) -> str:
    name = name.strip().lower()
    return _ALIASES.get(name, name)


def parse_hotkey(spec: str) -> tuple[frozenset[str], str]:
    partes = [normalize(p) for p in spec.split("+") if p.strip()]
    if not partes:
        raise ValueError(f"Atalho inválido: {spec!r}")
    principal = partes[-1]
    modificadores = frozenset(partes[:-1])
    desconhecidos = modificadores - _MODIFIERS
    if desconhecidos:
        raise ValueError(
            f"Modificador desconhecido em {spec!r}: {', '.join(sorted(desconhecidos))}. "
            f"Use um de: {', '.join(sorted(_MODIFIERS))}."
        )
    return modificadores, principal


def key_name(key) -> str | None:
    """Converte uma tecla do pynput num nome canônico."""
    from pynput import keyboard

    if isinstance(key, keyboard.Key):
        nome = key.name
        for lado in ("_l", "_r", "_gr"):
            if nome.endswith(lado):
                nome = nome[: -len(lado)]
                break
        return normalize(nome)

    vk = getattr(key, "vk", None)
    if vk in _MAC_VK:
        return _MAC_VK[vk]
    char = getattr(key, "char", None)
    if char:
        return normalize(char)
    return None


class HotkeyPushToTalk:
    """Implementa o Protocol PushToTalk."""

    def __init__(self, spec: str = "ctrl+space", mode: str = "hold") -> None:
        self.spec = spec
        self.mode = mode
        self._mods, self._main = parse_hotkey(spec)
        self._held: set[str] = set()
        self._active = False
        self._listener = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._keys_seen = 0
        self._started_at = 0.0

    # ---------- diagnóstico ----------

    @property
    def keys_seen(self) -> int:
        """Quantas teclas o listener enxergou. Zero = provável falta de permissão."""
        return self._keys_seen

    def seconds_since_start(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    def describe(self) -> str:
        rotulo = "segure para falar" if self.mode == "hold" else "aperte para começar, aperte para parar"
        return f"{self.spec}  ({rotulo})"

    # ---------- listener (thread do pynput) ----------

    def _emit(self, event: PTTEvent) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def _combo_held(self) -> bool:
        return self._main in self._held and self._mods.issubset(self._held)

    def _on_press(self, key) -> None:
        self._keys_seen += 1
        nome = key_name(key)
        if nome is None:
            return
        self._held.add(nome)
        if not self._combo_held():
            return

        if self.mode == "toggle":
            if nome == self._main:
                self._active = not self._active
                self._emit(PTTEvent.DOWN if self._active else PTTEvent.UP)
        elif not self._active:
            self._active = True
            self._emit(PTTEvent.DOWN)

    def _on_release(self, key) -> None:
        self._keys_seen += 1
        nome = key_name(key)
        if nome is None:
            return
        self._held.discard(nome)
        if self.mode == "hold" and self._active and not self._combo_held():
            self._active = False
            self._emit(PTTEvent.UP)

    # ---------- ciclo de vida ----------

    async def start(self) -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise RuntimeError("pynput não está instalado:  pip install pynput") from exc

        self._loop = asyncio.get_running_loop()
        self._started_at = time.monotonic()
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release, suppress=False
        )
        self._listener.start()

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, _SENTINEL)

    async def events(self) -> AsyncIterator[PTTEvent]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                return
            yield item
