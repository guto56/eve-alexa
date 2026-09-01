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
    "Apple_Terminal": ("Terminal", "/System/Applications/Utilities/Terminal.app"),
    "iTerm.app": ("iTerm", "/Applications/iTerm.app"),
    "vscode": ("Visual Studio Code", "/Applications/Visual Studio Code.app"),
    "WarpTerminal": ("Warp", "/Applications/Warp.app"),
    "Hyper": ("Hyper", "/Applications/Hyper.app"),
    "ghostty": ("Ghostty", "/Applications/Ghostty.app"),
    "WezTerm": ("WezTerm", "/Applications/WezTerm.app"),
    "alacritty": ("Alacritty", "/Applications/Alacritty.app"),
}


def host_app() -> str:
    """O app que precisa receber a permissão — não é o Python."""
    entrada = _TERMINALS.get(os.environ.get("TERM_PROGRAM", ""))
    return entrada[0] if entrada else "o seu aplicativo de terminal"


def host_app_path() -> str | None:
    """Caminho do app, para adicionar à mão quando ele não aparece na lista."""
    entrada = _TERMINALS.get(os.environ.get("TERM_PROGRAM", ""))
    return entrada[1] if entrada else None


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
        f"Ative {host_app()} nessa lista e reinicie o terminal — "
        "a permissão só vale a partir do próximo processo."
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


_MIC_INDETERMINADO, _MIC_RESTRITO, _MIC_NEGADO, _MIC_AUTORIZADO = 0, 1, 2, 3


def _mic_status_code() -> int | None:
    if not IS_MACOS:
        return None
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore

        return int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    except Exception:
        return None


def check_microphone() -> PermissionStatus:
    path = "Ajustes do Sistema › Privacidade e Segurança › Microfone"
    hint = f"Ative {host_app()} nessa lista."
    if not IS_MACOS:
        return PermissionStatus("Microfone", None, path, "só se aplica ao macOS")

    codigo = _mic_status_code()
    if codigo is None:
        return PermissionStatus(
            "Microfone", None, path,
            "não foi possível consultar — falta pyobjc-framework-AVFoundation",
        )
    if codigo == _MIC_AUTORIZADO:
        return PermissionStatus("Microfone", True, path, hint)
    if codigo == _MIC_INDETERMINADO:
        return PermissionStatus(
            "Microfone", None, path,
            "ainda não foi pedida — rode `python -m eve.apps.cli permissions`",
        )
    return PermissionStatus("Microfone", False, path, hint)


def request_microphone(timeout_s: float = 30.0) -> bool:
    """Dispara o diálogo do sistema e espera a resposta.

    O completion handler do AVFoundation roda em outra fila, então o resultado
    vem por polling do status em vez de por callback — num CLI não há run loop
    do AppKit para entregar o callback.
    """
    import time

    if not IS_MACOS:
        return True
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore

        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda concedido: None
        )
    except Exception:
        return False

    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        codigo = _mic_status_code()
        if codigo in (_MIC_AUTORIZADO, _MIC_NEGADO, _MIC_RESTRITO):
            return codigo == _MIC_AUTORIZADO
        time.sleep(0.25)
    return False


def microphone_instructions() -> str:
    """O painel de Microfone é diferente do de Monitoramento de Entrada: ele não
    tem botão +. Um app só aparece ali depois de pedir a permissão, e se já foi
    negada uma vez o macOS nunca mais pergunta — daí o tccutil."""
    return "\n".join([
        "O painel do Microfone NÃO tem botão + — não dá para adicionar o app à mão.",
        "Ele só lista quem já pediu a permissão. Se a permissão foi negada uma vez,",
        "o macOS não pergunta de novo. Para forçar a pergunta:",
        "",
        "    tccutil reset Microphone",
        "",
        "e rode `python -m eve` outra vez: o diálogo aparece na primeira gravação.",
        "",
        "Confira também Ajustes do Sistema › Som › Entrada: se o volume de entrada",
        "estiver no zero, a captura devolve silêncio digital mesmo com permissão.",
    ])


def manual_instructions() -> str:
    """A lista do macOS nasce vazia: um app só aparece nela depois de pedir a
    permissão pelo menos uma vez. Quem chega antes disso encontra "Nenhum Item"
    e acha que instalou algo errado."""
    caminho = host_app_path()
    linhas = [
        "A lista de Monitoramento de Entrada começa vazia — um app só aparece",
        "nela depois de pedir a permissão. Dois caminhos:",
        "",
        "  1. Rode `python -m eve`. Ele dispara o pedido, o macOS mostra o",
        "     diálogo e o seu terminal passa a aparecer na lista.",
        "",
        "  2. Ou adicione à mão: clique no + , aperte Cmd+Shift+G e cole",
    ]
    if caminho:
        linhas.append(f"     {caminho}")
    else:
        linhas += [
            "     o caminho do seu terminal, por exemplo:",
            "     /System/Applications/Utilities/Terminal.app   (Terminal)",
            "     /Applications/iTerm.app                       (iTerm)",
        ]
    linhas += [
        "",
        "Nos dois casos: ative o interruptor e depois FECHE E REABRA o terminal.",
        "A permissão só vale a partir do próximo processo.",
    ]
    return "\n".join(linhas)


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
