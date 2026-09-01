# EVE

Assistente de voz pessoal. **Fase 1 / M0: validação do caminho de áudio.**

Ainda não há STT, LLM nem TTS. O M0 responde uma pergunta só: *o áudio do seu
Mac funciona ponta a ponta, e com que qualidade?*

Arquitetura e decisões: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

## Instalação

Requer Python 3.11+ e macOS.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Não é preciso `brew install portaudio`: a roda de macOS do `sounddevice` já traz
a `libportaudio.dylib` embutida.

## Uso

```bash
python -m eve                     # inicia a EVE
python -m eve.apps.cli devices    # lista os dispositivos de áudio
python -m eve.apps.cli doctor     # checa dependências, permissões e devices
python -m eve.apps.cli permissions # pede a permissão de teclado (macOS)
```

Segure `CTRL+SPACE`, fale, solte. Você ouve a própria gravação e vê o turno:

```
── Turn 1 ──────────────────────────────────────────────
  Audio:     4.20 s      (134400 bytes · 16000 Hz · mono · PCM16)
  Capture:   4210 ms   (abertura do stream: 7 ms)
  Playback:  4238 ms
  Level:     pico -8.4 dBFS · rms -24.1 dBFS
  Total:     8461 ms
  Status:    OK
```

## Permissões do macOS

Ambas são concedidas ao **aplicativo de terminal** (Terminal, iTerm, VS Code,
Warp), nunca ao binário do Python — e **só valem depois de reiniciar o terminal**.

| Permissão | Onde | Sem ela |
|---|---|---|
| Monitoramento de Entrada | Ajustes › Privacidade e Segurança › Monitoramento de Entrada | O atalho não faz nada, e nenhum erro aparece |
| Microfone | Ajustes › Privacidade e Segurança › Microfone | A captura devolve silêncio digital |

`python -m eve.apps.cli doctor` diz o que está faltando.

### "A lista de Monitoramento de Entrada está vazia"

É o normal. **Um app só aparece nessa lista depois de pedir a permissão.**
Rode `python -m eve.apps.cli permissions`: ele dispara o pedido e o seu terminal
passa a aparecer lá. Ou adicione à mão — clique no `+`, aperte `Cmd+Shift+G` e
cole o caminho do seu terminal:

| Terminal | Caminho |
|---|---|
| Terminal | `/System/Applications/Utilities/Terminal.app` |
| iTerm | `/Applications/iTerm.app` |
| VS Code | `/Applications/Visual Studio Code.app` |
| Warp | `/Applications/Warp.app` |

Dos dois jeitos: ative o interruptor e **feche e reabra o terminal**.

Se o macOS não mostrar o diálogo, é porque ele só pergunta uma vez por app —
`tccutil reset ListenEvent` reseta e faz ele perguntar de novo.

## `ctrl+space` no macOS

É também o atalho de sistema para "Selecionar a fonte de entrada anterior". Com
mais de um layout de teclado instalado, ele troca o layout junto. Desative em
Ajustes › Teclado › Atalhos de Teclado › Fontes de entrada, ou mude a tecla em
`config/profiles/default.yaml`.

## Configuração

`config/profiles/default.yaml`. Outro perfil: `EVE_PROFILE=<nome> python -m eve`.

## Testes

```bash
pip install pytest && python -m pytest tests -q
```

Os testes usam dublês no lugar do hardware, então rodam em qualquer máquina.
