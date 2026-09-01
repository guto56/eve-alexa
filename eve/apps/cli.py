"""CLI do EVE.

    python -m eve                       inicia (equivale a `run`)
    python -m eve.apps.cli devices      lista dispositivos de áudio
    python -m eve.apps.cli doctor       checa dependências e permissões
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys

from eve import __version__
from eve.core.audio import AudioFormat
from eve.core.config import Config
from eve.core.events import TurnState

SEM_TECLA_APOS_S = 20.0


# ---------------------------------------------------------------- devices ----

def cmd_devices(_: argparse.Namespace) -> int:
    from eve.client import devices

    try:
        print(devices.render_table())
    except RuntimeError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    return 0


# ----------------------------------------------------------------- doctor ----

def cmd_doctor(_: argparse.Namespace) -> int:
    from eve.client import permissions

    print(f"EVE {__version__} · diagnóstico\n")

    print("Dependências")
    ok = True
    for modulo, pacote in (
        ("numpy", "numpy"),
        ("sounddevice", "sounddevice"),
        ("pynput", "pynput"),
        ("yaml", "pyyaml"),
    ):
        try:
            __import__(modulo)
            print(f"  [ ok  ] {pacote}")
        except ImportError as exc:
            ok = False
            # Instalado mas sem conseguir carregar é um problema diferente de
            # não estar instalado, e o conserto também é outro.
            import importlib.util

            instalado = importlib.util.find_spec(modulo) is not None
            if instalado:
                print(f"  [ERRO ] {pacote} está instalado mas não carrega: {exc}")
            else:
                print(f"  [FALTA] {pacote}  →  pip install {pacote}")
        except Exception as exc:
            ok = False
            print(f"  [ERRO ] {pacote}: {type(exc).__name__}: {exc}")
    print()

    if permissions.IS_MACOS:
        print(permissions.render_report())
        print()
    else:
        print(f"Sistema: {sys.platform} — as checagens de permissão são específicas do macOS.\n")

    print("Dispositivos")
    try:
        from eve.client import devices

        entrada = devices.resolve(None, want_input=True)
        saida = devices.resolve(None, want_input=False)
        print(f"  entrada padrão: {entrada.name} ({entrada.default_samplerate} Hz)")
        print(f"  saída padrão:   {saida.name} ({saida.default_samplerate} Hz)")
    except RuntimeError as exc:
        ok = False
        print(f"  [FALTA] {exc}")

    return 0 if ok else 1


# -------------------------------------------------------------------- run ----

def _preflight(cfg: Config) -> bool:
    """Bloqueia a partida só quando dá para afirmar que o atalho não vai funcionar."""
    from eve.client import permissions

    if not permissions.IS_MACOS:
        return True

    status = permissions.check_input_monitoring()
    if status.granted is not False:
        return True

    print("O atalho global não vai funcionar: falta permissão.\n")
    print(f"  {status.settings_path}")
    print(f"  {status.hint}\n")
    print("Abrindo o diálogo do sistema...")
    permissions.request_input_monitoring()
    print(
        "\nConceda a permissão, feche e reabra "
        f"{permissions.host_app()}, e rode `python -m eve` de novo."
    )
    return False


async def _run(cfg: Config) -> int:
    from eve.client import devices, permissions
    from eve.client.capture import MicrophoneSource
    from eve.client.playback import SpeakerSink
    from eve.client.ptt import HotkeyPushToTalk
    from eve.client.voice_client import VoiceClient
    from eve.agent.orchestrator import Orchestrator
    from eve.transport.inprocess import InProcessTransport

    fmt = AudioFormat(sample_rate=cfg.audio.sample_rate, channels=1, frame_ms=cfg.audio.frame_ms)

    try:
        dev_in = devices.resolve(cfg.audio.input_device, want_input=True)
        dev_out = devices.resolve(cfg.audio.output_device, want_input=False)
    except RuntimeError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1

    source = MicrophoneSource(dev_in, fmt)
    sink = SpeakerSink(dev_out)
    ptt = HotkeyPushToTalk(cfg.ptt.key, cfg.ptt.mode)
    transport = InProcessTransport()

    # Negocia a taxa agora para que a linha "Input:" já mostre a verdade.
    try:
        source.prepare()
    except Exception as exc:
        print(f"erro ao abrir o microfone: {exc}", file=sys.stderr)
        return 1

    print(f"EVE {__version__} · M0 — validação de áudio\n")
    print(f"  Input:   {source.describe()}")
    print(f"  Output:  {sink.describe()}")
    print(f"  Hotkey:  {ptt.describe()}")
    if permissions.IS_MACOS and "space" in cfg.ptt.key and "ctrl" in cfg.ptt.key:
        print(
            "\n  Nota: no macOS, ctrl+space também alterna a fonte de entrada do teclado.\n"
            "        Se atrapalhar, desative em Ajustes › Teclado › Atalhos › Fontes de\n"
            "        entrada, ou troque a tecla em config/profiles/default.yaml."
        )
    print("\nEVE READY")
    print(f"Pressione {cfg.ptt.key.upper()} para falar · Ctrl+C para sair")

    client = VoiceClient(
        transport, ptt, source, sink,
        on_state=lambda s: _print_state(s),
        on_report=lambda texto: print(texto),
    )
    core = Orchestrator(transport, fmt)

    # Ctrl+C vira um Event em vez de uma exceção subindo pelo meio de um await:
    # assim o desligamento fecha os streams de áudio de forma ordenada, em vez
    # de deixar o dispositivo preso até o processo morrer.
    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sinal, parar.set)

    tarefas = [
        asyncio.create_task(client.run()),
        asyncio.create_task(core.run()),
        asyncio.create_task(_watchdog(ptt)),
    ]
    try:
        await parar.wait()
    finally:
        print("\ntchau.")
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        await client.close()
    return 0


def _print_state(state: TurnState) -> None:
    rotulo = {
        TurnState.LISTENING: "● gravando...",
        TurnState.SPEAKING: "▶ reproduzindo...",
    }.get(state)
    if rotulo:
        print(rotulo)


async def _watchdog(ptt) -> None:
    """Se nenhuma tecla chegou ao listener, o problema é permissão, não código."""
    from eve.client import permissions

    await asyncio.sleep(SEM_TECLA_APOS_S)
    if ptt.keys_seen == 0 and permissions.IS_MACOS:
        print(
            "\n  aviso: em "
            f"{int(SEM_TECLA_APOS_S)} s o listener de teclado não recebeu nenhuma tecla.\n"
            "         Se você já digitou algo nesse tempo, falta Monitoramento de Entrada:\n"
            "         Ajustes do Sistema › Privacidade e Segurança › Monitoramento de Entrada\n"
            f"         Ative {permissions.host_app()} e reinicie o terminal.\n"
        )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load(args.profile)
    if not _preflight(cfg):
        return 1
    try:
        return asyncio.run(_run(cfg))
    except KeyboardInterrupt:      # se o handler de sinal não pegou
        return 0


# ------------------------------------------------------------------ main -----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eve", description="EVE — assistente de voz (M0)")
    parser.add_argument("--profile", default=None, help="perfil em config/profiles (padrão: default)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="inicia a EVE (padrão)").set_defaults(func=cmd_run)
    sub.add_parser("devices", help="lista os dispositivos de áudio").set_defaults(func=cmd_devices)
    sub.add_parser("doctor", help="checa dependências e permissões").set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args.command = "run"
        args.func = cmd_run
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
