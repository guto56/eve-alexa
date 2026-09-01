"""Configuração.

Um YAML opcional em config/profiles/<perfil>.yaml sobrescreve os defaults. Nada
de chave de API aqui: essas vêm só de variável de ambiente (no M0 não há
nenhuma, mas a regra vale desde já).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = PROJECT_ROOT / "config" / "profiles"


@dataclass(frozen=True)
class AudioConfig:
    input_device: int | str | None = None    # None = padrão do sistema
    output_device: int | str | None = None
    sample_rate: int = 16_000
    frame_ms: int = 20


@dataclass(frozen=True)
class PttConfig:
    backend: str = "pynput"
    key: str = "ctrl+space"
    mode: str = "hold"                        # hold | toggle


@dataclass(frozen=True)
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    ptt: PttConfig = field(default_factory=PttConfig)

    @staticmethod
    def load(profile: str | None = None) -> "Config":
        profile = profile or os.environ.get("EVE_PROFILE", "default")
        cfg = Config()
        path = PROFILES_DIR / f"{profile}.yaml"
        if not path.exists():
            return cfg

        try:
            import yaml
        except ImportError:
            return cfg

        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "audio" in raw:
            cfg = replace(cfg, audio=replace(cfg.audio, **raw["audio"]))
        if "ptt" in raw:
            cfg = replace(cfg, ptt=replace(cfg.ptt, **raw["ptt"]))
        return cfg
