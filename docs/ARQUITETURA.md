# EVE — Arquitetura (Fase 1: validação de experiência no PC)

> Documento de decisão. Nada implementado ainda. Objetivo desta fase: **descobrir se conversar com o EVE é bom**, não construir hardware.

---

## 1. TL;DR das decisões

| Decisão | Escolha | Por quê |
|---|---|---|
| Linguagem | Python 3.11+ / asyncio | Único ecossistema com áudio + wake word + STT local + todos os SDKs maduros |
| Modelo de conversa | Pipeline em cascata streaming (STT→LLM→TTS) | Speech-to-speech monolítico é mais rápido mas amarra você a um fornecedor — mata o requisito de trocar provider |
| Fronteira cliente/servidor | Definida agora, executada em um processo só | `Transport` é um adaptador; virar 2 processos é flag de config, não reescrita |
| Wake word / VAD | Sempre no cliente, sempre local | Se a decisão de "quando ouvir" depender da rede, o dispositivo fica burro e caro |
| LLM | `claude-opus-5`, streaming, fast mode, effort baixo, prompt cache | Melhor uso de ferramentas; fast mode dá até 2.5x mais tokens/s — importante para voz |
| Tools | Registry nativo + ponte MCP | MCP evita inventar formato de plugin; dispositivos futuros entram sem código novo |
| Memória | SQLite + sqlite-vec, escrita fora do caminho crítico | Latência zero no turno; dado do usuário separado do código |
| Personalidade | Arquivos YAML/Markdown versionados no git | Ciclo de vida diferente da memória: um é entrada do sistema, o outro é dado pessoal |
| AEC (eco) | Fones na Fase 1, interface pronta para WebRTC APM depois | É o problema mais subestimado de assistente de voz; não vale queimar as primeiras semanas nele |

---

## 2. Análise e premissas

O repositório está vazio — é greenfield. As restrições reais que moldam o desenho vêm do seu enunciado:

1. **"Streaming e baixa latência desde o início"** — isso é uma restrição de arquitetura, não uma otimização. Um sistema desenhado com chamadas request/response não vira streaming depois; você o reescreve. Todo contrato entre camadas é um `AsyncIterator`.
2. **"Preparada para separar cliente de servidor"** — a fronteira precisa existir no código desde já, mesmo rodando num processo. Se não existir, ela nunca aparece.
3. **"Trocar provider sem reescrever"** — interface não basta. O que garante isso é uma **suíte de testes de contrato** que toda implementação precisa passar.
4. **"Memória separada da personalidade"** — decisão certa e frequentemente ignorada. Detalho na seção 12.
5. **"Terminal primeiro, sem GUI/hardware"** — correto. A GUI esconde latência e a latência é justamente o que você quer sentir.

---

## 3. A decisão central: cascata vs. speech-to-speech

Existem dois caminhos hoje e eles levam a arquiteturas incompatíveis.

**A) Speech-to-speech nativo** (APIs realtime que recebem áudio e devolvem áudio, sem texto no meio).
- ✅ Latência de ~300–600ms, turn-taking natural, entonação preserva emoção da sua fala.
- ❌ Um único fornecedor faz STT+LLM+TTS. Você não troca peça: troca o sistema inteiro.
- ❌ Controle fraco sobre tools, memória e prompt. Difícil de rodar local. Caro por minuto de áudio.

**B) Cascata com streaming em cada estágio.**
- ✅ Cada camada é trocável. Roda local se você quiser. Controle total de tools/memória/persona.
- ✅ Você vê o texto — dá para debugar, logar, testar, medir.
- ❌ ~800ms–1.5s até o primeiro som se bem feito (e 3s+ se mal feito).

**Recomendação: B.** Seus dois requisitos explícitos — trocar providers e ter tools/memória próprias — são exatamente o que A destrói. A diferença de latência é real mas administrável (seção 8).

**Mitigação inteligente:** o orquestrador conversa com uma interface `ConversationEngine`, não com STT/LLM/TTS diretamente. Existem duas implementações possíveis: `CascadeEngine` (composta pelas três camadas) e, no futuro, `RealtimeEngine` (um provider speech-to-speech inteiro). Assim você pode testar A depois sem tocar no orquestrador, nas tools ou na memória. Não construa `RealtimeEngine` agora — só deixe o encaixe.

---

## 4. Blocos

```
┌─────────────────── CLIENTE (vira dispositivo depois) ───────────────────┐
│  Microfone → Ring Buffer → AEC → VAD → Wake Word                        │
│  Alto-falante ← Jitter Buffer ←──────────────────────────────┐          │
└────────────────────────────┬─────────────────────────────────┼──────────┘
                             │ Transport (in-process hoje, WebSocket depois)
                             │ áudio PCM16 20ms + eventos JSON
┌────────────────────────────▼─────────────────────────────────┼──────────┐
│                      SERVIDOR DE IA                          │          │
│                                                              │          │
│   STT (streaming) ──parciais/final──► ORQUESTRADOR ──texto──► TTS ──────┘
│                                            │  ▲   (streaming) (streaming)
│                                            ▼  │                          │
│                                       Agente LLM (streaming + tools)     │
│                                            │  ▲                          │
│                          ┌─────────────────┴──┴────────────────┐         │
│                          │  Tool Registry  │  Memória  │ Persona│         │
│                          │  nativas + MCP  │  SQLite   │  YAML  │         │
│                          └─────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────────┘
```

Regra de fronteira: **tudo que precisa do microfone em tempo real fica no cliente** (captura, AEC, VAD, wake word, playback). Tudo que precisa de CPU/GPU/rede/chaves fica no servidor.

---

## 5. Contratos (o núcleo da arquitetura)

Definidos como `typing.Protocol` — sem herança, sem framework de DI. Todos são assíncronos e streaming.

```python
class AudioSource(Protocol):
    def frames(self) -> AsyncIterator[AudioFrame]: ...   # PCM16 mono 16k, 20ms

class AudioSink(Protocol):
    async def write(self, frame: AudioFrame) -> None: ...
    async def flush(self) -> None: ...
    async def stop(self) -> None: ...                    # descarta buffer (barge-in)

class WakeWordDetector(Protocol):
    def detect(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[WakeEvent]: ...

class VAD(Protocol):
    def segment(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SpeechEvent]: ...
    # SpeechEvent: SPEECH_START | SPEECH_END(confidence, silence_ms)

class STTProvider(Protocol):
    def transcribe(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[Transcript]: ...
    # Transcript(text, is_final, stability, ts)

class LLMProvider(Protocol):
    def respond(self, ctx: Context) -> AsyncIterator[LLMEvent]: ...
    # LLMEvent: TextDelta | ToolCall | ToolResultNeeded | Done(usage)

class TTSProvider(Protocol):
    def synthesize(self, text: AsyncIterator[str]) -> AsyncIterator[AudioFrame]: ...

class ConversationEngine(Protocol):
    def run_turn(self, audio_in, audio_out, ctx) -> AsyncIterator[TurnEvent]: ...
```

Três invariantes que valem mais que o resto do documento:

1. **Nenhum método retorna uma lista completa.** Se retornasse, você teria embutido latência na assinatura.
2. **Todo estágio aceita cancelamento** (`asyncio.CancelledError` limpo, sem deixar socket pendurado). Barge-in é cancelamento propagado da ponta ao servidor.
3. **Todo estágio emite marcos de tempo** para o `TurnTrace`. Latência que você não mede, você não corrige.

---

## 6. Orquestrador: máquina de estados

```
        wake / push-to-talk
IDLE ──────────────────────► LISTENING
 ▲                              │ VAD end-of-utterance
 │                              ▼
 │                          THINKING ──(tool call)──► TOOL_RUNNING
 │                              │  ◄─────────────────────┘
 │                              │ primeiro chunk de texto
 │                              ▼
 └──── fim do áudio ───────  SPEAKING
                                │
        fala do usuário durante SPEAKING → BARGE_IN → LISTENING
```

- **Barge-in** é obrigatório, não é enfeite: `audio_sink.stop()`, cancela o stream do TTS, cancela o stream do LLM, guarda a resposta parcial no histórico marcada como interrompida ("você foi interrompido depois de dizer X" — o modelo precisa saber disso ou repete tudo).
- Turnos são **cancelável por construção**: cada turno é uma `asyncio.Task`; interromper é `task.cancel()`.
- O orquestrador é a única peça que conhece o estado global. STT, LLM e TTS não sabem que existe uma conversa.

---

## 7. Orçamento de latência

Alvo do primeiro som depois que você para de falar:

| Etapa | Custo típico | Alavanca |
|---|---|---|
| VAD confirma fim de turno | **250–500ms** | **maior e mais controlável** |
| STT devolve final | 80–200ms | provider; áudio já foi processado em streaming |
| LLM primeiro token | 350–700ms | fast mode, effort baixo, cache de prompt |
| Primeiro áudio do TTS | 80–150ms | provider + tamanho do primeiro chunk |
| Buffer de playback | 50–100ms | tamanho do jitter buffer |
| **Total** | **~0.8–1.5s** | |

Referência de percepção: abaixo de 800ms parece conversa; 1–2s é aceitável; acima de 2s a pessoa começa a falar por cima.

Técnicas, em ordem de retorno:

1. **Endpointing adaptativo.** Silêncio de 200ms se o parcial termina em algo sintaticamente completo; 600–800ms se termina em hesitação ou conjunção ("e...", "tipo...", "então"). É aqui que se ganha meio segundo — mais do que em qualquer otimização de modelo.
2. **Chunk por cláusula, com o primeiro curto.** Manda para o TTS na primeira vírgula/ponto ou aos ~40 caracteres; chunks seguintes maiores (150–200 chars) para a prosódia não ficar picotada. O primeiro chunk curto é o que derruba o tempo até o primeiro som.
3. **Sockets quentes.** Conexões WebSocket com STT e TTS abertas e mantidas com keepalive. Handshake custa 100–300ms por turno se você reconectar.
4. **Prompt caching.** Prefixo estável (tools → persona → perfil do usuário) cacheado; o retrieval volátil vai *depois* do breakpoint. Corta TTFT e custo.
5. **Nunca esperar o LLM terminar.** O TTS consome o stream de tokens.
6. *(Opcional, medir depois)* **Execução especulativa**: disparar o LLM no parcial estável e cancelar se o final divergir. Ganha 200–400ms, custa tokens duplicados. Deixe atrás de uma flag.

---

## 8. Áudio, e o problema que todo mundo subestima

Com alto-falante aberto, o microfone ouve o próprio EVE. Consequências: o wake word dispara com a própria voz e o barge-in aciona sozinho. Isso é cancelamento de eco acústico (AEC), e é o problema mais chato do projeto inteiro.

Opções:

1. **Fones na Fase 1.** O problema some e você valida a experiência conversacional, que é o objetivo declarado. **Recomendado para M0–M3.**
2. **WebRTC APM / speexdsp** com o sinal de playback como referência. Funciona, mas exige captura e playback no mesmo clock de dispositivo — com devices diferentes o drift de clock destrói o AEC.
3. **Half-duplex** (mutar o mic enquanto fala). Não faça: mata o barge-in, que é metade da sensação de "conversa".

Decisão: interface `EchoCanceller` no `audio/`, implementação no-op por padrão, fones na Fase 1, WebRTC APM quando o hardware real entrar (M4+). Isso também reforça por que AEC é responsabilidade do **cliente**: ele precisa do sinal de referência do alto-falante local.

Outros detalhes de áudio que causam bug real: taxa fixa de 16kHz mono na captura (resample no cliente, não no servidor), frames de 20ms, ring buffer com pre-roll de ~500ms (para o STT receber o começo da palavra que disparou o wake word), e captura em thread separada alimentando a asyncio loop — captura de áudio bloqueia e não pode competir com o event loop.

---

## 9. Wake word

**openWakeWord** (local, gratuito, ~1% de CPU). Alternativa: Porcupine (mais preciso, licença paga acima de poucos usuários).

Dois avisos concretos:

- **"EVE" sozinho é curto demais** — duas fonemas, taxa alta de falso positivo em fala normal. Use **"Ei, EVE"** ou **"Ok, EVE"**. Isso não é detalhe estético: define se o sistema é usável no dia a dia.
- **Verificação em dois estágios.** O detector dispara → o orquestrador abre o STT → se em 1,5s não vier fala plausível, volta para IDLE silenciosamente. Corta a maioria dos falsos positivos sem precisar de um modelo melhor.

Não treine wake word customizado agora. Comece com um modelo pronto e troque depois — a interface `WakeWordDetector` isola isso.

---

## 10. Providers recomendados para a Fase 1

Todos atrás de interface; a escolha abaixo é o *default*, não um compromisso.

| Camada | Default Fase 1 | Alternativa local | Alternativa cloud |
|---|---|---|---|
| Wake word | openWakeWord | — (já é local) | Porcupine |
| VAD | Silero VAD (local, ~1ms) | — | — |
| STT | Deepgram Nova (streaming, pt-BR bom, ~150ms) | faster-whisper `large-v3-turbo` **exige GPU** | OpenAI `gpt-4o-transcribe` |
| LLM | `claude-opus-5` | Ollama (Qwen/Llama) via mesma interface | — |
| TTS | ElevenLabs Flash v2.5 (~75ms TTFB, pt-BR natural) | Piper (rápido, mas robótico em pt-BR) | Cartesia Sonic |

Observações honestas:
- `faster-whisper` **em CPU não serve** para tempo real com qualidade — `medium` fica acima de 1x realtime na maioria dos PCs. Sem GPU, STT é cloud.
- Piper em pt-BR é utilizável mas não é "natural". Se o objetivo é validar a experiência, comece com ElevenLabs Flash e mantenha Piper como fallback offline.
- Cada provider declara suas capacidades (`supports_streaming`, `supports_interim`, `languages`) para o orquestrador degradar em vez de quebrar.

---

## 11. Camada LLM (detalhes que importam para voz)

Modelo: **`claude-opus-5`** via SDK oficial `anthropic`, streaming sempre.

```python
stream = client.beta.messages.stream(
    model="claude-opus-5",
    max_tokens=1024,                          # respostas de voz são curtas
    speed="fast",                             # até 2.5x tokens/s
    betas=["fast-mode-2026-02-01", "server-side-fallback-2026-07-01"],
    fallbacks="default",                      # nunca ficar mudo por recusa
    output_config={"effort": "low"},          # conversa casual não precisa de effort alto
    thinking={"type": "adaptive"},
    system=[...],                             # persona + perfil, com cache_control
    messages=[...],
    tools=[...],
)
```

Racional de cada escolha:

- **Fast mode** (`speed="fast"`, Opus 5 / 4.8 apenas, preview): custa mais por token mas aumenta bastante os tokens/s. Em voz, tokens/s vira "o EVE fala sem engasgar". Vale para o caminho conversacional; não vale para tarefas de fundo (extração de memória).
- **`effort: "low"`** com thinking adaptativo. Turno de conversa não é problema difícil. Suba para `medium`/`high` só quando o turno envolver ferramenta complexa — dá para decidir por rota.
- **Não desabilite o thinking.** No Opus 5, thinking desligado ocasionalmente faz o modelo escrever a chamada de ferramenta como texto visível em vez de emitir o bloco `tool_use` — em voz isso vira o EVE falando "vou chamar a função tocar_musica" em voz alta. Effort baixo resolve o custo/latência sem esse risco.
- **`fallbacks: "default"`** com o beta de fallback do servidor: se uma requisição for recusada por classificador, o servidor roteia sozinho. Um assistente de voz não pode ficar em silêncio.
- **Prompt caching** com ordem `tools → system → messages`: qualquer byte alterado no prefixo invalida tudo depois. Persona e definições de tools são estáveis → ficam no prefixo cacheado. Retrieval de memória e hora atual são voláteis → vão depois do último breakpoint.
- **Mensagens `system` no meio da conversa** (suportado em Opus 5) para injetar contexto operacional — "o usuário te interrompeu", "agora são 22h" — sem invalidar o prefixo cacheado.

**Loop de tools:** comece com `client.beta.messages.tool_runner` (hooks por turno dão o gate de permissão e a interceptação de erro). Se o cancelamento por barge-in ficar desconfortável dentro do runner, caia para o loop manual — é uma decisão contida dentro de `AnthropicProvider`, invisível para o resto do sistema.

---

## 12. Tools

```python
@tool(
    name="ajustar_volume",
    risk=Risk.SAFE,                  # SAFE | CONFIRM | BLOCKED
    budget=Budget.INSTANT,           # INSTANT <300ms | SHORT <3s | LONG async
    filler="só um segundo",          # falado enquanto roda, se SHORT
)
async def ajustar_volume(nivel: int) -> str: ...
```

Três pontos de desenho que existem por causa da voz:

1. **Orçamento de tempo é parte da declaração da tool.** Silêncio de 8 segundos numa conversa por voz é ruptura total. `INSTANT` roda invisível; `SHORT` fala um filler; `LONG` responde "te aviso quando terminar" e dispara um turno de fala não solicitado depois — o que exige que o orquestrador saiba **iniciar um turno sem input do usuário**. Isso precisa estar no desenho desde já, não é retrofit.
2. **Permissão por risco, configurada fora do código.** Comando de voz é entrada não autenticada: qualquer som na sala pode virar comando. `CONFIRM` exige confirmação falada. **Não crie uma tool genérica de shell na Fase 1** — tools estreitas e específicas.
3. **MCP como protocolo de extensão.** Um `MCPToolSource` conecta a servidores MCP locais (stdio) e registra as tools deles no mesmo registry, com o mesmo esquema de risco. Home Assistant, arquivos, música, calendário entram sem código novo — é exatamente o caminho para "futuramente controlar dispositivos". Tools nativas só para o que exige latência mínima ou acesso ao áudio local. (Nota: o conector MCP da API é server-side; aqui você quer um **cliente MCP local**, porque as ações acontecem no seu PC.)

---

## 13. Memória vs. Personalidade

Você pediu essa separação e ela está certa. O motivo é ciclo de vida:

| | Personalidade | Memória |
|---|---|---|
| O que é | Entrada do sistema | Dado do usuário |
| Onde vive | `persona/*.yaml` versionado no git | `eve.db` (SQLite), fora do git |
| Quem edita | Você, iterando prompt | O EVE, em runtime |
| Como se testa | Diff, revisão, rollback | Inspeção, edição, apagar |
| Se apagar | `git checkout` | Perdeu de verdade — precisa de backup |

Misturar os dois é o erro clássico: você acaba sem conseguir versionar a personalidade nem apagar dados pessoais.

### Personalidade

```yaml
# persona/eve.yaml
name: EVE
language: pt-BR
identity: >
  Assistente pessoal do Gustavo. Direta, sem enrolação.
voice_style:
  max_sentences: 3
  avoid: [listas numeradas, markdown, emojis, "Como posso ajudar?"]
  register: informal
behaviors:
  - se não souber, diga que não sabe
  - antes de ação destrutiva, confirme
tts:
  provider: elevenlabs
  voice_id: "..."
```

Um template renderiza isso no system prompt. **Regra dura:** o LLM por padrão escreve para tela — listas, markdown, parágrafos. Isso arruína a experiência de voz. Além da instrução no prompt, o orquestrador tem um limitador: se a resposta passar de N frases, ele registra e a próxima instrução do sistema aperta. Trocar persona = trocar arquivo, sem tocar em código.

### Memória

SQLite único (`eve.db`) com `sqlite-vec` para embeddings. Não precisa de banco vetorial dedicado nessa escala — e não precisará tão cedo.

```sql
sessions(id, started_at, ended_at)
turns(id, session_id, ts, role, text, latency_json)        -- episódica, append-only
facts(id, key, value, confidence, pinned, source_turn_id,
      created_at, updated_at, expires_at)                   -- semântica / perfil
fact_embeddings(fact_id, embedding)                         -- sqlite-vec
summaries(id, session_id, period, text, embedding)          -- consolidação
```

Quatro camadas em runtime:

1. **Working memory** — últimos N turnos em RAM, limitados por tokens.
2. **Bloco de perfil** — facts `pinned` ou de alta importância, sempre injetados (~200 tokens). Mudam raramente → ficam no prefixo cacheado; escrever um fato invalida o cache uma vez, o que é aceitável.
3. **Retrieval** — top-k por similaridade sobre `facts` + `summaries`, injetado depois do breakpoint de cache.
4. **Escrita** — **sempre fora do caminho crítico.** Depois do turno, uma task de fundo roda um extrator (modelo barato, `claude-haiku-4-5`) sobre o turno, propõe fatos, deduplica por similaridade e faz upsert. Escrita de memória **nunca** adiciona latência ao turno.

Além da extração automática, tools explícitas: `lembrar(fato)`, `esquecer(consulta)`, `o_que_voce_sabe_sobre(consulta)`. Isso dá ao usuário controle por voz sobre o que o assistente guarda — o que importa para confiança, não só para funcionalidade.

---

## 14. Transporte e a fronteira cliente/servidor

Um WebSocket, frames binários para áudio e JSON para controle.

- **Subida:** PCM16 mono 16kHz, frames de 20ms (320 amostras / 640 bytes). Opus quando houver rede de verdade.
- **Descida:** PCM16 mono 24kHz (saída típica de TTS) ou Opus.
- **Controle:** `wake`, `eou` (fim de fala), `barge_in`, `state`, `transcript{partial,text}`, `speak_start`, `speak_end`, `error`.

Regra de ouro: **o cliente decide quando começar e parar de ouvir.** VAD e wake word no servidor significam pagar RTT em cada decisão de turno e ter um dispositivo inútil sem rede.

Implementação: `Transport` como Protocol, com `InProcessTransport` (filas asyncio, latência zero) e `WebSocketTransport`. A Fase 1 roda in-process; M4 vira dois processos com uma flag de config. Como as interfaces são as mesmas dos dois lados, não há reescrita — é essa a razão de definir a fronteira agora.

---

## 15. Configuração e troca de providers

```yaml
# config/profiles/cloud.yaml
stt:  {provider: deepgram,   model: nova-3,     language: pt-BR}
llm:  {provider: anthropic,  model: claude-opus-5, effort: low, fast: true}
tts:  {provider: elevenlabs, voice_id: "...",   model: flash_v2_5}
wake: {provider: openwakeword, phrase: "ei eve", threshold: 0.6}
vad:  {provider: silero, min_silence_ms: 300, adaptive: true}
```

`EVE_PROFILE=local|cloud` troca tudo. Registry de fábricas por nome; nenhuma classe concreta importada fora do seu módulo.

**O que realmente torna providers trocáveis não é a interface — é o teste de contrato.** `tests/contracts/test_stt.py` roda contra *qualquer* implementação de `STTProvider` com um WAV fixo e verifica: emite parciais, emite final, respeita cancelamento, fecha sem vazar socket, reporta latência. Um provider novo só entra se passar. Sem isso, "arquitetura trocável" é ficção — e a descoberta acontece no pior momento.

---

## 16. Observabilidade

Cada turno gera um `TurnTrace` com marcos temporais, impresso no terminal:

```
turno 7  fala 1.84s │ stt_final +118ms │ llm_ttft +402ms │ tts_ttfb +91ms │ 1º áudio 611ms │ total 3.2s │ 340 tok │ $0.004
```

Isso é entrega do **M0**, não de "algum dia". O objetivo declarado da fase é avaliar a experiência, e a experiência é dominada por latência. Sem a cascata de tempos, você vai otimizar por palpite.

---

## 17. Estrutura do repositório

```
eve/
  core/       contracts.py (Protocols) · events.py · config.py · trace.py
  audio/      capture.py · playback.py · resample.py · aec.py · ring.py
  wake/       base.py · openwakeword_detector.py
  vad/        base.py · silero.py
  stt/        base.py · deepgram.py · whisper_local.py
  llm/        base.py · anthropic_provider.py · ollama_provider.py
  tts/        base.py · elevenlabs.py · piper.py
  agent/      orchestrator.py · turn.py (máquina de estados) · context.py · chunker.py
  tools/      registry.py · permissions.py · builtin/ · mcp_bridge.py
  memory/     store.py · retrieval.py · extraction.py · schema.sql
  persona/    eve.yaml · render.py
  transport/  base.py · inprocess.py · ws_server.py · ws_client.py
  apps/       cli.py · server.py · client.py
config/       default.yaml · profiles/{local,cloud}.yaml
tests/        contracts/ · integration/
```

Um pacote, várias camadas. Não quebre em múltiplos repositórios agora — o custo de coordenação não se paga antes do M4.

---

## 18. Roadmap

| Marco | Entrega | O que prova |
|---|---|---|
| **M0** | Áudio in/out + `TurnTrace` + loop push-to-talk que só ecoa o transcript | O stack de áudio funciona no seu SO |
| **M1** | Cascata STT→LLM→TTS streaming, push-to-talk, sem tools | **Aqui você sente a latência real e decide se o caminho é viável** |
| **M2** | Wake word + endpointing adaptativo + barge-in | Vira hands-free — é aqui que parece um assistente |
| **M3** | Tool registry + ponte MCP + memória + persona | Vira útil |
| **M4** | Transporte WebSocket, dois processos na mesma máquina | Prova que a fronteira do dispositivo é real |
| **M5** | Segundo provider por camada + testes de contrato + benchmark de latência/custo | Decide o que vai para o hardware |

Ordem deliberada: M1 vem antes de wake word porque push-to-talk elimina AEC e falso positivo da equação e deixa você medir a latência pura da cascata. Se M1 não ficar bom, nada depois salva.

---

## 19. O que NÃO fazer na Fase 1

GUI · hardware/ESP32 · Docker · multiusuário · RAG sobre documentos · treinar wake word customizado · clonar voz · autenticação · Kubernetes · fila de mensagens · microserviços.

Cada um deles adiciona semanas e nenhum responde à pergunta "conversar com o EVE é bom?".

---

## 20. Riscos, em ordem de probabilidade de te atrapalhar

1. **Endpointing em português.** O sistema corta você no meio da frase enquanto você pensa. É a maior fonte de frustração em assistentes de voz — mais do que latência bruta. Vale mais tempo do que otimizar 100ms de TTFT.
2. **AEC.** Fones adiam o problema; o hardware o traz de volta inteiro. Planejado, não resolvido.
3. **O LLM escreve para tela, não para voz.** Resposta de 5 parágrafos lida em voz alta é insuportável. Prompt + limitador, e verifique a cada mudança de persona.
4. **Tools longas.** Sem o desenho de turno assíncrono, qualquer ação de 10s quebra a conversa.
5. **Custo em nuvem.** Wake word local evita streaming contínuo, mas meça desde o M1 — o `TurnTrace` já carrega o custo por turno.
6. **Falsos positivos de wake word.** "Ei, EVE" + verificação em dois estágios. Um sistema que acorda sozinho é abandonado em uma semana.

---

## 21. Perguntas em aberto (mudam o desenho)

1. **Sistema operacional?** Muda o stack de áudio (PipeWire / WASAPI / CoreAudio) e a viabilidade do AEC.
2. **Tem GPU?** Define se `faster-whisper` local é opção real ou se STT é obrigatoriamente cloud.
3. **Nuvem é aceitável, ou local-first por privacidade?** Muda o default de todas as três camadas.
4. **Fones ou alto-falante aberto na Fase 1?** Define se AEC entra no M2 ou no M4.
