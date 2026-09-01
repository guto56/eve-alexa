"""Permissões do macOS.

Duas coisas podem falhar em silêncio no macOS, e ambas parecem bug do programa:

* Sem "Monitoramento de Entrada", o listener de teclado do pynput não recebe
  nada e não levanta erro — a tecla simplesmente não faz nada.
* Sem acesso ao Microfone, a captura devolve silêncio digital em vez de falhar.

Este módulo detecta as duas antes de o usuário perder tempo achando que quebrou.
A permissão é concedida ao aplicativo de terminal que hospeda o Python, nunca ao
binário do Python — e exige reiniciar o terminal depois.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

IS_MACOS = sys.platform == "darwin"

_TERMINALS = {
    "Apple_Terminal": "Terminal",
    "iTerm.app": "iTerm",
    "vscode": "Visual Studio Code (ou Code Helper)",
    "WarpTerminal": "Warp",
    "Hyper": "Hyper",
    "ghostty": "Ghostty",
    "WezTerm": "WezTerm",
    "alacritty": "Alacritty",
}


def host_app() -> str:
    """O app que precisa receber a permissão — não é o Python."""
    return _TERMINALS.get(os.environ.get("TERM_PROGRAM", ""), "o seu aplicativo de terminal")


@dataclass(frozen=True)
class PermissionStatus:
    name: str
    granted: bool | None          # None = não foi possível determinar
    settings_path: str
    hint: str

    @property
    def symbol(self) -> str:
        return {True: "ok", False: "FALTA", None: "?"}[self.granted]


def check_input_monitoring() -> PermissionStatus:
    """Monitoramento de Entrada — necessário para o atalho global."""
    path = "Ajustes do Sistema › Privacidade e Segurança › Monitoramento de Entrada"
    hint = (
        f"Adicione e ative {host_app()} nessa lista e "
        "reinicie o terminal — a permissão só vale a partir do próximo processo."
    )
    if not IS_MACOS:
        return PermissionStatus("Monitoramento de Entrada", None, path, "só se aplica ao macOS")
    try:
        from Quartz import CGPreflightListenEventAccess  # type: ignore
    except Exception:
        return PermissionStatus("Monitoramento de Entrada", None, path, hint)
    try:
        return PermissionStatus("Monitoramento de Entrada", bool(CGPreflightListenEventAccess()), path, hint)
    except Exception:
        return PermissionStatus("Monitoramento de Entrada", None, path, hint)


def request_input_monitoring() -> bool:
    """Dispara o diálogo do sistema. Retorna True se já estava concedida."""
    if not IS_MACOS:
        return True
    try:
        from Quartz import CGRequestListenEventAccess  # type: ignore

        return bool(CGRequestListenEventAccess())
    except Exception:
        return False


def check_microphone() -> PermissionStatus:
    """Microfone. Best-effort: só é determinável se pyobjc-framework-AVFoundation
    estiver instalado. Sem ele, o sinal de verdade é o nível do áudio capturado."""
    path = "Ajustes do Sistema › Privacidade e Segurança › Microfone"
    hint = (
        f"Ative {host_app()} nessa lista. Na primeira captura o macOS costuma "
        "perguntar sozinho; se você já negou uma vez, ele não pergunta de novo."
    )
    if not IS_MACOS:
        return PermissionStatus("Microfone", None, path, "só se aplica ao macOS")
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore

        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        # 0 indeterminado · 1 restrito · 2 negado · 3 autorizado
        if status == 3:
            return PermissionStatus("Microfone", True, path, hint)
        if status in (1, 2):
            return PermissionStatus("Microfone", False, path, hint)
        return PermissionStatus("Microfone", None, path, "ainda não foi pedida — será pedida na primeira captura")
    except Exception:
        return PermissionStatus("Microfone", None, path, hint)


def report() -> list[PermissionStatus]:
    return [check_input_monitoring(), check_microphone()]


def render_report() -> str:
    linhas = ["Permissões do macOS"]
    for st in report():
        linhas.append(f"  [{st.symbol:^5}] {st.name}")
        if st.granted is not True:
            linhas.append(f"          {st.settings_path}")
            linhas.append(f"          {st.hint}")
    return "\n".join(linhas)
