"""Eventos que cruzam a fronteira Voice Client <-> EVE Core.

Hoje os dois lados rodam no mesmo processo, mas todo diálogo entre eles passa
por estes tipos. É este conjunto — e só ele — que vira WebSocket quando o Voice
Client sair para um dispositivo separado.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from eve.core.audio import AudioFrame


class TurnState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


# ---------- Voice Client -> EVE Core ----------

@dataclass(frozen=True)
class PttDown:
    """A tecla foi pressionada: começa a captura.

    open_ms é quanto o cliente levou para armar o microfone. Vive no evento
    porque é o cliente que sabe — e no M0 essa é justamente a medida que diz
    se abrir o stream por turno corta a primeira sílaba.
    """

    open_ms: float | None = None


@dataclass(frozen=True)
class AudioChunk:
    frame: AudioFrame


@dataclass(frozen=True)
class PttUp:
    """A tecla foi solta: fim da fala."""


@dataclass(frozen=True)
class SpeakDone:
    """O cliente terminou de reproduzir tudo o que recebeu neste turno."""


@dataclass(frozen=True)
class Shutdown:
    reason: str = ""


ClientEvent = PttDown | AudioChunk | PttUp | SpeakDone | Shutdown


# ---------- EVE Core -> Voice Client ----------

@dataclass(frozen=True)
class State:
    value: TurnState


@dataclass(frozen=True)
class AudioOut:
    frame: AudioFrame


@dataclass(frozen=True)
class SpeakEnd:
    """Não há mais áudio para este turno."""


@dataclass(frozen=True)
class Notice:
    text: str


@dataclass(frozen=True)
class TurnComplete:
    report: str


CoreEvent = State | AudioOut | SpeakEnd | Notice | TurnComplete
