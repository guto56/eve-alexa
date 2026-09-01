"""Descoberta e resolução de dispositivos de áudio (CoreAudio via PortAudio)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    max_input: int
    max_output: int
    default_samplerate: int
    is_default_input: bool
    is_default_output: bool

    @property
    def kinds(self) -> list[str]:
        out = []
        if self.max_input > 0:
            out.append("in")
        if self.max_output > 0:
            out.append("out")
        return out


def _sd():
    try:
        import sounddevice as sd
    except OSError as exc:  # PortAudio ausente
        raise RuntimeError(
            "Não foi possível carregar a PortAudio.\n"
            "No macOS a roda do sounddevice já traz a libportaudio.dylib — se ela "
            "faltou, reinstale com:  pip install --force-reinstall sounddevice\n"
            f"Detalhe: {exc}"
        ) from exc
    return sd


def list_devices() -> list[DeviceInfo]:
    sd = _sd()
    default_in, default_out = sd.default.device
    devices = []
    for i, d in enumerate(sd.query_devices()):
        devices.append(
            DeviceInfo(
                index=i,
                name=d["name"],
                max_input=d["max_input_channels"],
                max_output=d["max_output_channels"],
                default_samplerate=int(d["default_samplerate"]),
                is_default_input=(i == default_in),
                is_default_output=(i == default_out),
            )
        )
    return devices


def resolve(spec: int | str | None, *, want_input: bool) -> DeviceInfo:
    """Aceita índice, trecho do nome, ou None para o padrão do sistema."""
    devices = list_devices()
    if not devices:
        raise RuntimeError("Nenhum dispositivo de áudio encontrado.")

    if spec is None:
        for d in devices:
            if (want_input and d.is_default_input) or (not want_input and d.is_default_output):
                return d
        candidatos = [d for d in devices if (d.max_input if want_input else d.max_output) > 0]
        if not candidatos:
            raise RuntimeError(
                "Nenhum dispositivo de " + ("entrada" if want_input else "saída") + " disponível."
            )
        return candidatos[0]

    if isinstance(spec, int):
        for d in devices:
            if d.index == spec:
                return d
        raise RuntimeError(f"Dispositivo de índice {spec} não existe. Use: python -m eve.apps.cli devices")

    alvo = spec.lower()
    for d in devices:
        canais = d.max_input if want_input else d.max_output
        if canais > 0 and alvo in d.name.lower():
            return d
    raise RuntimeError(f"Nenhum dispositivo casa com {spec!r}. Use: python -m eve.apps.cli devices")


def render_table() -> str:
    devices = list_devices()
    if not devices:
        return "Nenhum dispositivo de áudio encontrado."

    largura = max(len(d.name) for d in devices)
    linhas = [
        f"{'IDX':>4}  {'E/S':<7} {'CANAIS':>6}  {'TAXA':>10}  NOME",
        f"{'':>4}  {'':<7} {'':>6}  {'':>10}  " + "─" * largura,
    ]
    for d in devices:
        kinds = "/".join(d.kinds) or "—"
        canais = max(d.max_input, d.max_output)
        marca = []
        if d.is_default_input:
            marca.append("padrão in")
        if d.is_default_output:
            marca.append("padrão out")
        sufixo = f"   ← {', '.join(marca)}" if marca else ""
        linhas.append(
            f"{d.index:>4}  {kinds:<7} {canais:>6}  {d.default_samplerate:>7} Hz  {d.name}{sufixo}"
        )
    return "\n".join(linhas)
