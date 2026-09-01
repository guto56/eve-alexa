"""O reamostrador só entra em ação quando o dispositivo recusa 16 kHz, mas
quando entra ele fica no caminho de todo áudio — um erro aqui parece defeito de
microfone. Daí os testes de duração e de continuidade entre blocos."""

from __future__ import annotations

import numpy as np
import pytest

from eve.client.resample import Resampler


def _seno(rate: int, seconds: float, freq: int = 440, amp: float = 0.6) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype(np.int16)


@pytest.mark.parametrize("src", [48_000, 44_100, 32_000, 22_050, 8_000])
def test_duracao_preservada(src: int) -> None:
    r = Resampler(src, 16_000)
    bloco = max(1, src // 50)                      # 20 ms
    entrada = _seno(src, 2.0)
    saida = np.concatenate(
        [r.process(entrada[i : i + bloco]) for i in range(0, len(entrada) - bloco + 1, bloco)]
    )
    esperado = 2.0 * 16_000
    assert abs(len(saida) - esperado) / esperado < 0.005


def test_passthrough_nao_toca_no_audio() -> None:
    r = Resampler(16_000, 16_000)
    assert not r.active
    pcm = _seno(16_000, 0.1)
    assert np.array_equal(r.process(pcm), pcm)


def test_sem_estalo_entre_blocos() -> None:
    """Processar em blocos de 20 ms tem que dar quase o mesmo que de uma vez.
    Se a cauda entre blocos fosse descartada, apareceria um degrau a cada 20 ms."""
    src = 48_000
    entrada = _seno(src, 0.5)
    inteiro = Resampler(src, 16_000).process(entrada)

    r = Resampler(src, 16_000)
    bloco = src // 50
    partido = np.concatenate(
        [r.process(entrada[i : i + bloco]) for i in range(0, len(entrada), bloco)]
    )
    n = min(len(inteiro), len(partido))
    erro = np.abs(inteiro[:n].astype(np.int32) - partido[:n].astype(np.int32)).max()
    assert erro <= 1, f"descontinuidade entre blocos: {erro}"


def test_upsample_para_saida() -> None:
    """O alto-falante pode recusar 16 kHz; nesse caso subimos a taxa."""
    r = Resampler(16_000, 48_000)
    assert r.active
    saida = np.concatenate([r.process(_seno(16_000, 0.02)) for _ in range(50)])
    assert abs(len(saida) - 48_000) / 48_000 < 0.005
