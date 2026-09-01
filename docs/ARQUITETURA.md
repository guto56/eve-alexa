# EVE — Arquitetura (Fase 1: MVP push-to-talk, 100% API, Windows 11)

> Documento de decisão, v2. Nada implementado ainda.
> Objetivo desta fase: **fazer `tecla → fala → resposta em voz natural` ficar excelente**, gastando o mínimo possível, sem comprar nada.

---

## 0. O que mudou da v1

| | v1 | v2 |
|---|---|---|
| Hardware | pressupunha fones | **microfone e alto-falante do próprio PC, nada externo** |
| Modelos locais | faster-whisper, Piper, openWakeWord como opções | **removidos da Fase 1** — tudo via API |
| LLM | SDK Anthropic direto | **OpenRouter** (superfície compatível com OpenAI) |
| Entrada de voz | wake word + VAD desde o M2 | **push-to-talk no MVP**; wake word/VAD viram Fase 2 |
| Eco / AEC | risco nº 2, mitigado com fones | **dissolvido pelo push-to-talk** (§4) |
| SO | genérico | **Windows 11**, com o que isso implica (§9) |
| Custo | não era seção | **seção própria (§12)** — "gastar o mínimo" virou requisito de projeto |

O que **não** mudou: a separação Voice Client / EVE Core, os contratos streaming, a separação memória × personalidade e a regra de que a fronteira do dispositivo existe no código desde o primeiro dia.

---

## 1. Decisões

| Decisão | Escolha | Por quê |
|---|---|---|
| Linguagem | Python 3.11+ / asyncio | Áudio, SDKs e HTTP streaming no mesmo lugar; roda bem em Windows |
| Interação | **Push-to-talk** (segurar tecla) | Elimina wake word, VAD, endpointing e eco de uma vez só |
| Modelo de conversa | Cascata: STT → LLM → TTS | Requisito de trocar provider por configuração |
| STT | **Batch** (áudio completo no release) | PTT te dá o fim da fala de graça; streaming vira otimização do M2 |
| LLM → TTS | **Streaming obrigatório** | É o único ponto onde streaming é inegociável no MVP |
| LLM | OpenRouter, default `anthropic/claude-haiku-4.5` | Um endpoint, muitos modelos, troca por config; Haiku tem o menor TTFT útil |
| TTS | Azure Neural pt-BR (free tier) ou ElevenLabs Flash | Tensão real entre "natural" e "barato" — resolvida em §11 |
| Fronteira | Voice Client / EVE Core, um processo só | Conceitual agora, dois processos depois, sem reescrita |
| Eco / AEC | Fora do caminho crítico | PTT resolve; AEC volta quando entrar hands-free |
| Memória | Interface agora, implementação no M3 | Costura pronta, sem construir o que você ainda não precisa |

---

## 2. Premissas da Fase 1

1. **Nenhum hardware novo.** Microfone integrado (ou o que já estiver plugado) e alto-falantes do PC. Windows 11.
2. **Nenhum modelo local.** Sem GPU no orçamento, sem download de pesos, sem CUDA. Tudo API.
3. **Custo mínimo.** O sistema precisa custar poucos dólares por mês em uso pessoal, e isso é uma restrição de projeto — não um detalhe operacional.
4. **Terminal, sem GUI.** A interface é uma tecla e o alto-falante.
5. **Uma pessoa, uma máquina.** Sem multiusuário, sem autenticação, sem deploy.

---

## 3. O MVP, em uma frase

> Seguro uma tecla → falo → solto → a EVE entende, pensa e responde em voz natural pelo alto-falante.

Nada além disso entra na Fase 1. O sucesso é medido por duas perguntas:

- **É rápido?** Menos de ~1,5 s entre soltar a tecla e ouvir o primeiro som.
- **É bom de conversar?** A voz soa natural, as respostas são curtas, e você não fica repetindo porque ela não entendeu.

Se as duas forem "sim", o projeto está provado e o resto é expansão. Se qualquer uma for "não", nenhuma feature adicional salva.

---

## 4. Por que push-to-talk primeiro — e o que ele resolve de graça

Você propôs PTT para reduzir complexidade. Ele faz mais que isso: **elimina quatro problemas de uma vez**, e três deles eram os principais riscos da v1.

| Problema | Como o PTT resolve |
|---|---|
| **Eco acústico (AEC)** | O microfone só abre enquanto você segura a tecla. A EVE nunca ouve a si mesma. O problema não é adiado — ele não existe no MVP. |
| **Endpointing** (o assistente te cortar no meio da frase) | Você decide quando terminou. Zero ambiguidade, zero ajuste de silêncio, zero frustração. Era o risco nº 1 da v1. |
| **Falso positivo de wake word** | Não há wake word. A EVE nunca acorda sozinha. |
| **Barge-in** | Apertar a tecla enquanto ela fala corta o áudio na hora. Interrupção determinística, sem detecção de voz, sem falso positivo. |

O custo dessa escolha é uma coisa só: não é hands-free. Para validar a experiência conversacional no PC, isso não importa.

**Consequência de projeto:** o AEC não é "adiado com risco". Ele passa a ser um requisito da Fase 2, que só aparece junto com wake word e VAD — as três coisas que criam a necessidade dele. É a ordem certa.

---

## 5. Blocos: Voice Client e EVE Core

```
┌───────── VOICE CLIENT (vira o dispositivo na Fase 3) ─────────┐
│                                                                │
│   Hotkey global ──► Captura (WASAPI, 16 kHz mono)             │
│         │                      │                               │
│         │ ptt_up               │ buffer PCM16 em RAM           │
│         ▼                      ▼                               │
│   Playback ◄──── Jitter buffer ◄──────────────────────┐        │
└────────────────────────┬──────────────────────────────┼────────┘
                         │  Transport                   │
                         │  (filas asyncio hoje,        │
                         │   WebSocket na Fase 2)       │
┌────────────────────────▼──────────────────────────────┼────────┐
│                      EVE CORE                         │        │
│                                                       │        │
│   STT (batch, HTTP) ──texto──► ORQUESTRADOR ──chunks──┴► TTS   │
│                                     │  ▲                       │
│                                     ▼  │                       │
│                            Agente LLM (OpenRouter, streaming)  │
│                                     │  ▲                       │
│                        ┌────────────┴──┴────────────┐          │
│                        │ Tools │ Memória │ Persona  │          │
│                        └────────────────────────────┘          │
└────────────────────────────────────────────────────────────────┘
```

**Regra de fronteira (inalterada da v1):** o que precisa do microfone e do alto-falante em tempo real fica no Voice Client. O que precisa de chaves de API, CPU ou estado fica no EVE Core.

**O que cruza a fronteira:**

| Direção | Evento | Payload |
|---|---|---|
| ↑ | `ptt_down` | — |
| ↑ | `audio_chunk` | PCM16 mono 16 kHz, 20 ms |
| ↑ | `ptt_up` | — (marca fim da fala) |
| ↓ | `state` | `IDLE` \| `LISTENING` \| `THINKING` \| `SPEAKING` |
| ↓ | `transcript` | o que a EVE entendeu (para o terminal) |
| ↓ | `audio_out` | PCM16 mono 24 kHz |
| ↓ | `speak_end` | — |

São sete eventos. É esse contrato que vira WebSocket depois — e é por isso que ele precisa estar escrito agora, mesmo com os dois lados no mesmo processo.

---

## 6. Contratos

`typing.Protocol`, sem herança nem framework. O que muda em relação à v1: `STTProvider` ganha um método batch (o caminho do MVP) e mantém o streaming como opcional para o M2.

```python
class AudioSource(Protocol):
    def frames(self) -> AsyncIterator[AudioFrame]: ...     # PCM16 mono 16k, 20 ms

class AudioSink(Protocol):
    async def write(self, frame: AudioFrame) -> None: ...
    async def stop(self) -> None: ...                      # descarta o buffer (barge-in)

class PushToTalk(Protocol):
    def events(self) -> AsyncIterator[PTTEvent]: ...       # DOWN | UP

class STTProvider(Protocol):
    async def transcribe(self, audio: bytes) -> Transcript: ...          # MVP
    def transcribe_stream(self, frames) -> AsyncIterator[Transcript]: ... # M2, opcional

class LLMProvider(Protocol):
    def respond(self, ctx: Context) -> AsyncIterator[LLMEvent]: ...
    # LLMEvent: TextDelta | ToolCall | Done(usage)

class TTSProvider(Protocol):
    def synthesize(self, text: AsyncIterator[str]) -> AsyncIterator[AudioFrame]: ...

class MemoryStore(Protocol):
    async def recall(self, query: str, k: int) -> list[Fact]: ...
    async def remember(self, fact: Fact) -> None: ...
```

Invariantes que continuam valendo:

1. **`LLMProvider` e `TTSProvider` nunca devolvem tudo pronto.** Se devolvessem, você teria embutido latência na assinatura, e nenhuma otimização posterior a tira de lá.
2. **Todo estágio aceita cancelamento** — `CancelledError` limpo, sem socket pendurado.
3. **Todo estágio emite marcos de tempo** para o `TurnTrace` (§14).

`STTProvider.transcribe` é `async` mas não streaming — e isso é deliberado. Com PTT você já tem o áudio completo; streaming ali resolveria um problema que você não tem.

---

## 7. Máquina de estados (versão PTT)

```
         tecla pressionada
IDLE ─────────────────────────► LISTENING
 ▲                                  │ tecla solta
 │                                  ▼
 │                              THINKING ──(tool)──► TOOL_RUNNING
 │                                  │  ◄──────────────────┘
 │                                  │ 1º chunk de texto
 │                                  ▼
 └────── fim do áudio ─────────  SPEAKING
                                    │
     tecla pressionada durante SPEAKING
     → corta o áudio, cancela LLM e TTS → LISTENING
```

Bem mais simples que a v1: sem `WAKE`, sem estados de detecção, sem timeout de falso positivo. Cinco estados, transições determinísticas, nenhuma delas dependente de heurística de áudio.

Um detalhe que parece pequeno e não é: **ao ser interrompida, a EVE guarda a resposta parcial no histórico marcada como interrompida.** Sem isso, o modelo não sabe o que já saiu pelo alto-falante e repete tudo do começo na resposta seguinte.

---

## 8. Latência

Alvo: tempo entre **soltar a tecla** e **ouvir o primeiro som**.

| Etapa | Custo típico | Observação |
|---|---|---|
| Áudio já está em RAM | ~0 ms | PTT: nada a esperar |
| Upload + STT batch (clipe de ~5 s) | 300–600 ms | Groq `whisper-large-v3-turbo` fica na ponta baixa |
| LLM primeiro token | 350–700 ms | inclui ~30–80 ms do hop do OpenRouter |
| Primeiro chunk de texto → primeiro áudio | 100–400 ms | ElevenLabs Flash ~100 ms; Azure/OpenAI 200–400 ms |
| Buffer de playback | 50–100 ms | |
| **Total** | **~0,85 – 1,8 s** | |

Comparado com a v1 (0,8–1,5 s), o teto piorou um pouco: o STT batch substitui o streaming e o OpenRouter adiciona um salto. Em troca, **a variância despencou** — sumiram o endpointing errado, o wake word que não dispara e o que dispara sozinho. Assistente de voz é julgado pela variância, não pela mediana: um sistema que responde em 1,3 s sempre parece melhor que um que responde em 0,9 s na maior parte das vezes e te corta no meio da frase de vez em quando.

**Alavancas, em ordem de retorno:**

1. **Chunk por cláusula, com o primeiro curto.** Manda para o TTS na primeira vírgula ou aos ~40 caracteres; chunks seguintes maiores (150–200) para a prosódia não picotar. É a maior alavanca do MVP.
2. **Conexão HTTP reaproveitada.** Um `httpx.AsyncClient` de vida longa por provider. Handshake TLS novo custa 100–300 ms por turno.
3. **Peça PCM cru ao TTS**, não MP3 (§9). Elimina o decoder do caminho crítico e uma dependência.
4. **Prompt curto e estável.** Persona + tools primeiro, contexto volátil depois — ajuda o cache do provider quando ele existe.
5. **Nunca esperar o LLM terminar.** O TTS consome o stream de tokens.
6. *(M2)* **Subir o áudio enquanto a tecla está pressionada**, para um STT streaming. O final chega ~100 ms depois do release em vez de 300–600 ms. Economiza 200–500 ms sem abandonar o PTT.

---

## 9. Áudio no Windows 11 — detalhes que causam dor

- **Captura e playback:** `sounddevice` (PortAudio) com backend WASAPI. Funciona bem no Windows 11 e não exige nada instalado além do pacote Python.
- **Hotkey global:** `pynput` — funciona com o terminal fora de foco e **não exige privilégio de administrador** (diferente da biblioteca `keyboard`, que exige em vários cenários). Segurar-para-falar via listener de press/release. Vale ter um modo alternativo de alternância (aperta uma vez para começar, outra para parar) porque "segurar" em terminal às vezes conflita com atalhos do Windows.
- **Peça PCM cru ao TTS.** ElevenLabs (`output_format=pcm_24000`), OpenAI (`response_format="pcm"`, 24 kHz 16-bit mono) e Azure (`Raw24Khz16BitMonoPcm`) devolvem PCM direto. Isso remove um decoder de MP3 do caminho crítico e evita depender de `ffmpeg`/`pyav` no Windows, que é onde a instalação costuma quebrar.
- **Escolha o dispositivo explicitamente.** Windows troca o default sozinho quando você pluga qualquer coisa. Fixe o índice do device na config e logue qual foi aberto.
- **Formatos fixos:** captura em 16 kHz mono PCM16, playback em 24 kHz mono PCM16. Resample no cliente, nunca no core.
- **Captura em thread separada** alimentando o event loop por fila. Callback de áudio bloqueia e não pode competir com o asyncio.

**Risco honesto: o microfone integrado do PC é a peça mais fraca do sistema.** Array de notebook é ruidoso e capta longe. Se a transcrição vier errada, a causa provável é o microfone, não o modelo de STT — troque de provider por último, não primeiro. Mitigações que custam zero: normalizar ganho antes de enviar, cortar silêncio nas pontas, e falar a ~30 cm do aparelho.

---

## 10. Providers da Fase 1 — tudo por API

| Camada | Default | Alternativa | Por quê |
|---|---|---|---|
| STT | **Groq** `whisper-large-v3-turbo` | OpenAI `gpt-4o-mini-transcribe`; Deepgram Nova-3 | Groq é o mais barato e um dos mais rápidos; Whisper vai bem em pt-BR |
| LLM | **OpenRouter** → `anthropic/claude-haiku-4.5` | `anthropic/claude-sonnet-5`, `google/gemini-2.5-flash` | Menor TTFT útil com boa qualidade em pt-BR e bom uso de ferramentas |
| TTS | **Azure Neural** pt-BR (free tier) | ElevenLabs Flash v2.5; OpenAI `tts-1` | Ver §11 — é a decisão que envolve dinheiro de verdade |

Cada provider declara suas capacidades (`supports_streaming`, `languages`, `output_formats`) para o orquestrador degradar em vez de quebrar.

---

## 11. A tensão real: "voz natural" × "gastar o mínimo"

Vale dizer isso direto, porque é o único ponto do projeto onde os seus dois objetivos se contradizem.

**O TTS é o item mais caro do sistema — não o LLM.** E é exatamente o item que determina se a experiência parece natural, que é o que você quer validar.

| Provider | Qualidade pt-BR | Custo aproximado | Veredito |
|---|---|---|---|
| **Azure Neural** (vozes Francisca, Antônio, Thalita…) | Muito boa | **500 mil caracteres/mês grátis**, depois ~US$ 16/milhão | Melhor relação para o MVP. Exige conta Azure (cartão para verificação; o tier grátis não cobra) |
| **OpenAI `tts-1`** | Boa, sotaque levemente neutro | ~US$ 15/milhão de caracteres | Zero fricção se você já tem chave OpenAI. ~US$ 4/mês no uso estimado |
| **ElevenLabs Flash v2.5** | Melhor do mercado, e a mais rápida (~75 ms) | Plano Creator US$ 22/mês | Cara para uso contínuo |

**Recomendação:** comece no **Azure**. Se depois de uma semana a voz parecer o elo fraco da experiência, **pague um mês de ElevenLabs e faça A/B** — a mesma frase nas duas vozes, ouvindo. Como a interface de TTS é trocável por configuração, esse teste custa uma linha de YAML e US$ 22 uma vez. Validar se a voz barata é boa o bastante é um uso legítimo de dinheiro; assinar ElevenLabs antes de saber se o resto funciona, não é.

---

## 12. Custos

Estimativa para uso pessoal moderado: **50 turnos/dia**, ~6 s de fala por turno, ~180 caracteres de resposta.

Isso dá, por mês: ~2,5 h de áudio de entrada · ~270 mil caracteres de saída · ~900 mil tokens de entrada e ~195 mil de saída.

| Item | Escolha | Custo/mês estimado |
|---|---|---|
| STT | Groq `whisper-large-v3-turbo` | **~US$ 0,10** |
| TTS | Azure Neural (dentro do free tier de 500 mil) | **US$ 0,00** |
| LLM | OpenRouter → `anthropic/claude-haiku-4.5` | **~US$ 1,90** |
| | **Total** | **~US$ 2/mês** |

Se quiser trocar o cérebro por um mais forte, o impacto é menor do que parece:

| Modelo (via OpenRouter) | Custo/mês no mesmo uso |
|---|---|
| `google/gemini-2.5-flash` | ~US$ 0,80 |
| `anthropic/claude-haiku-4.5` | ~US$ 1,90 |
| `anthropic/claude-sonnet-5` | ~US$ 3,80 |
| `anthropic/claude-opus-5` | ~US$ 9,40 |

**A conclusão que importa: não economize no LLM.** Mesmo o modelo mais caro fica abaixo de US$ 10/mês nesse volume. O que decide a conta é o TTS, e é lá que o free tier do Azure resolve o problema. Se a qualidade do raciocínio incomodar, subir de Haiku para Sonnet custa dois dólares — decida ouvindo, depois que o M1 estiver de pé.

> Valores são ordem de grandeza, com preços de tabela de meados de 2026. Confira antes de assinar qualquer coisa. O `TurnTrace` (§14) registra o custo real por turno desde o M1, então em uma semana você troca essa estimativa por dado.

---

## 13. Camada LLM via OpenRouter

OpenRouter expõe uma superfície compatível com OpenAI. Use o SDK `openai` com a base URL trocada — não escreva HTTP na mão.

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

stream = await client.chat.completions.create(
    model="anthropic/claude-haiku-4.5",
    messages=[...],            # system: persona + perfil; depois o histórico
    tools=[...],               # formato de tools da OpenAI
    stream=True,
    max_tokens=400,            # resposta de voz é curta — isto é um limite real
    extra_body={
        "models": ["anthropic/claude-haiku-4.5",
                   "google/gemini-2.5-flash"],   # fallback automático
        "provider": {"sort": "latency"},          # roteia pelo mais rápido
    },
)
```

**O que você ganha:** um endpoint para todos os modelos, troca por string de configuração, fallback automático quando um provider cai, e uma fatura só. É exatamente o requisito de "trocar o LLM sem reescrever".

**O que você perde, e é honesto dizer:**

- **Um salto de rede extra** — ~30 a 80 ms no TTFT.
- **Recursos específicos de fornecedor.** Coisas como o formato nativo de cache de prompt da Anthropic, `effort`, ou o modo rápido do Opus não estão disponíveis pela superfície compatível com OpenAI. Se um dia você precisar deles, a interface `LLMProvider` permite adicionar um `AnthropicProvider` nativo ao lado, sem tocar no orquestrador.
- **Variância entre provedores.** O mesmo slug pode ser servido por infraestruturas diferentes, com latências diferentes. Use `provider: {sort: "latency"}` e, se incomodar, fixe o provedor.

**Regras de prompt que existem por causa da voz:**

- `max_tokens` baixo é funcional, não econômico: resposta longa lida em voz alta é insuportável.
- O system prompt precisa proibir markdown, listas numeradas e emojis explicitamente. O modelo escreve para tela por padrão, e isso arruína a experiência falada.
- Ordem estável: `tools` → persona → perfil → histórico → turno atual. Ajuda o cache onde ele existir e mantém o diff do prompt legível.

---

## 14. Observabilidade

Cada turno gera um `TurnTrace` impresso no terminal:

```
turno 7 │ áudio 4.2s │ stt 380ms │ llm_ttft 410ms │ tts_ttfb 190ms
        │ 1º som 990ms │ total 3.4s │ 512 tok │ US$ 0.0011
```

Entrega do **M0**, não de "algum dia". O objetivo declarado da fase é avaliar a experiência, e a experiência é dominada por latência e custo. Sem a cascata de tempos você otimiza por palpite — e palpite em latência de áudio erra quase sempre. Com ela, em uma semana você sabe qual estágio é o gargalo real da *sua* máquina e da *sua* conexão.

---

## 15. Tools, memória e persona — o que entra agora

Você pediu para adiar isso, e está certo. Mas a costura precisa existir para o M3 não virar reescrita.

**Tools — no MVP:** o registry existe, com uma ou duas tools triviais (`que_horas_sao`, `ajustar_volume`) só para provar o caminho de ida e volta. A declaração já carrega os dois campos que existem por causa da voz:

```python
@tool(name="ajustar_volume",
      risk=Risk.SAFE,            # SAFE | CONFIRM | BLOCKED
      budget=Budget.INSTANT)     # INSTANT <300ms | SHORT <3s | LONG async
async def ajustar_volume(nivel: int) -> str: ...
```

`budget` existe porque silêncio de 8 segundos numa conversa é ruptura total: `SHORT` fala um preenchimento, `LONG` responde "te aviso quando terminar" e dispara um turno depois. `risk` existe porque comando de voz é entrada não autenticada — qualquer som na sala pode virar comando. **Nada de tool genérica de shell.** A ponte MCP e o controle de dispositivos entram no M4, no mesmo registry.

**Memória — no MVP:** só working memory (últimos N turnos em RAM, limitados por tokens). A interface `MemoryStore` existe e tem uma implementação vazia. SQLite + `sqlite-vec`, extração de fatos em background e as tools `lembrar`/`esquecer` entram no M3.

**Persona — no MVP:** já vale a pena, porque é o que faz a EVE soar como EVE e custa um arquivo.

```yaml
# persona/eve.yaml
name: EVE
language: pt-BR
identity: >
  Assistente pessoal do Gustavo. Direta, sem enrolação.
voice_style:
  max_sentences: 3
  avoid: [markdown, listas numeradas, emojis, "Como posso ajudar?"]
  register: informal
behaviors:
  - se não souber, diga que não sabe
  - antes de ação destrutiva, confirme
```

A separação entre persona e memória continua sendo a mesma decisão da v1, e o motivo é ciclo de vida:

| | Personalidade | Memória |
|---|---|---|
| O que é | Entrada do sistema | Dado do usuário |
| Onde vive | `persona/*.yaml`, no git | `eve.db`, fora do git |
| Quem edita | Você, iterando o prompt | A EVE, em runtime |
| Se apagar | `git checkout` | Perdeu — precisa de backup |

---

## 16. Configuração e troca de providers

```yaml
# config/profiles/default.yaml
ptt:  {backend: pynput, key: ctrl+space, mode: hold}   # hold | toggle
audio:
  input_device: null        # null = default do Windows; fixe o índice depois
  output_device: null
stt:  {provider: groq, model: whisper-large-v3-turbo, language: pt}
llm:
  provider: openrouter
  model: anthropic/claude-haiku-4.5
  fallbacks: [google/gemini-2.5-flash]
  max_tokens: 400
tts:  {provider: azure, voice: pt-BR-FranciscaNeural, format: raw-24khz-16bit-mono-pcm}
```

Registry de fábricas por nome; nenhuma classe concreta importada fora do seu módulo. Chaves de API só por variável de ambiente — nunca no YAML, que vai para o git.

**O que realmente torna providers trocáveis não é a interface — é o teste de contrato.** `tests/contracts/test_tts.py` roda contra *qualquer* implementação de `TTSProvider` e verifica: devolve PCM na taxa declarada, emite o primeiro chunk antes do texto acabar, respeita cancelamento, fecha sem vazar conexão, reporta latência. Um provider novo só entra se passar. Sem isso, "arquitetura trocável" é uma promessa que você descobre ser falsa exatamente quando mais precisa dela.

---

## 17. Estrutura do repositório

```
eve/
  core/       contracts.py (Protocols) · events.py · config.py · trace.py
  client/     ptt.py · capture.py · playback.py · devices.py
  stt/        base.py · groq.py · openai.py
  llm/        base.py · openrouter.py
  tts/        base.py · azure.py · elevenlabs.py · openai.py
  agent/      orchestrator.py · turn.py · context.py · chunker.py
  tools/      registry.py · permissions.py · builtin/
  memory/     base.py · working.py           # sqlite.py entra no M3
  persona/    eve.yaml · render.py
  transport/  base.py · inprocess.py         # ws_*.py entram na Fase 2
  apps/       cli.py
config/       profiles/default.yaml
tests/        contracts/ · integration/
```

Um pacote, duas camadas conceituais (`client/` e o resto). Não quebre em repositórios separados agora — o custo de coordenação não se paga antes da Fase 2.

---

## 18. Roadmap

**Fase 1 — validar a experiência (é onde você está)**

| Marco | Entrega | O que prova |
|---|---|---|
| **M0** | Hotkey + captura + playback + `TurnTrace`, gravando e tocando de volta | O stack de áudio funciona no seu Windows, com o seu microfone |
| **M1** | STT → LLM → TTS streaming ponta a ponta | **É o MVP. Aqui você sente a latência e a voz reais e decide se o caminho vale.** |
| **M2** | Barge-in, chunking afinado, tratamento de erro, persona, tools triviais | Deixa de ser demo e vira algo que você usa todo dia |
| **M3** | Memória em SQLite, extração em background, tools `lembrar`/`esquecer` | A EVE passa a te conhecer |

**Fase 2 — hands-free (só depois que a Fase 1 estiver excelente)**

| Marco | Entrega |
|---|---|
| M4 | WebSocket entre Voice Client e EVE Core, dois processos |
| M5 | Wake word + VAD + AEC — os três juntos, porque um cria a necessidade do outro |
| M6 | Ponte MCP, smart home, controle do computador |

**Fase 3 — hardware.** Só depois de a Fase 2 estar estável. O Voice Client já estará isolado e falando WebSocket, que é o pré-requisito real.

M0 vem antes do M1 por um motivo prático: se o microfone integrado do seu PC for ruim demais, você descobre em uma tarde gravando e ouvindo, não depois de integrar três APIs.

---

## 19. O que NÃO fazer na Fase 1

GUI · wake word · VAD · AEC · modelos locais · hardware · Docker · RAG sobre documentos · smart home · controle complexo do computador · clonagem de voz · multiusuário · autenticação · fila de mensagens · microserviços.

Cada um adiciona semanas, e nenhum responde à única pergunta desta fase: *conversar com a EVE é bom?*

---

## 20. Riscos, em ordem de probabilidade

1. **Microfone integrado ruim.** O elo mais fraco e o mais barato de diagnosticar — por isso o M0 existe. Se a transcrição vier errada, suspeite do microfone antes do modelo.
2. **A voz não convence.** Você quer validar naturalidade e escolheu o caminho barato. Mitigação: A/B com ElevenLabs por um mês (§11), decidido ouvindo, não no papel.
3. **O LLM escreve para tela.** Cinco parágrafos lidos em voz alta são insuportáveis. Prompt + `max_tokens` baixo + verificação a cada mudança de persona.
4. **Latência acumulada em três APIs.** Três saltos de rede em série; conexão ruim multiplica tudo. O `TurnTrace` mostra qual estágio é o culpado.
5. **Ruído do alto-falante entrando na gravação.** Improvável com PTT, mas se você apertar a tecla enquanto a EVE ainda fala, ela se ouve. Mitigação trivial: `ptt_down` corta o playback *antes* de abrir o microfone.
6. **Rate limit ou instabilidade do free tier.** O tier grátis do Azure tem limite de concorrência. Para um usuário só, sobra — mas trate erro de TTS com um fallback falado, não com silêncio.

---

## 21. Perguntas em aberto

1. **Quais chaves de API você já tem?** (OpenRouter, Groq, Azure, OpenAI, ElevenLabs) — define o default de STT e TTS do M1.
2. **O atalho precisa funcionar com o terminal fora de foco?** Se sim, `pynput` global; se não, dá para simplificar mais ainda.
3. **Notebook ou desktop, e que microfone?** Muda o quanto o M0 precisa investigar antes de seguir.
