# Local LLM setup (Ollama)

MEMORY//OS runs in two honest modes:

| Mode | Label in the UI | What happens |
|---|---|---|
| Real | `REAL AGENT` | A local model decides which tools to call, reads the results and writes the answer. |
| Fallback | `DETERMINISTIC FALLBACK` | No model is reachable. A deterministic planner answers. Memory, embeddings and retrieval are still real. |

No API key and no paid service is ever required. The fallback is always
labelled as a fallback - it is never presented as intelligence.

---

## 1. Install Ollama

**Linux / macOS**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Or download a release directly from <https://github.com/ollama/ollama/releases>.

> **Version matters.** Use **0.6.x or newer**. Older builds mishandle tool
> calling with Qwen models: 0.1.x silently ignores the `tools` parameter, and
> 0.3.x returns the model's `<tool_call>` JSON as raw text instead of parsing it
> into structured calls. Both produce a broken agent loop.

## 2. Start the server

```bash
ollama serve
```

It listens on `http://127.0.0.1:11434` by default.

On a low-RAM machine, keep memory predictable:

```bash
OLLAMA_KEEP_ALIVE=15m OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

## 3. Pull a model

| Model | Size | RAM needed | Notes |
|---|---|---|---|
| `qwen2.5:0.5b-instruct-q4_K_M` | ~373 MB | ~1.0 GB | Verified working here. Reliable tool calls; extraction quality is modest. |
| `qwen2.5:1.5b-instruct` | ~1 GB | ~1.7 GB | **Recommended** when you have the RAM. Noticeably better extraction. |
| `qwen2.5:7b-instruct` | ~4.7 GB | ~6 GB | Best quality of the three. |

```bash
ollama pull qwen2.5:1.5b-instruct
```

Any tool-calling model works. Qwen2.5 Instruct is the default because its
tool-call formatting is dependable even at small sizes.

## 4. Point the app at it

Auto-detection is on by default: if `MODEL_PROVIDER` is unset and an Ollama
server is reachable with the configured model pulled, real agent mode turns
itself on. To be explicit:

```bash
export MODEL_PROVIDER=ollama
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen2.5:1.5b-instruct
```

Short names resolve against what you actually have installed, so
`qwen2.5:1.5b-instruct` matches a pulled `qwen2.5:1.5b-instruct-q4_K_M`.

Other variables:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_TIMEOUT_S` | `120` | Per-request timeout. Raise it on slow CPUs. |
| `LLM_NUM_CTX` | `4096` | Context window passed to the model. |

## 5. Health check

```bash
curl -s localhost:11434/api/tags | head          # is Ollama up?
curl -s localhost:8000/api/provider | python -m json.tool
```

Expected in real mode:

```json
{
  "provider": {
    "name": "ollama",
    "available": true,
    "model": "qwen2.5:1.5b-instruct-q4_K_M",
    "tool_calling": true,
    "mode": "REAL AGENT"
  }
}
```

The Observatory PROVIDER panel shows the same values.

## 6. Prove it is real

The real-agent tests skip themselves unless a model is genuinely reachable, so
they can never report a false pass:

```bash
cd backend
OLLAMA_BASE_URL=http://127.0.0.1:11434 \
OLLAMA_MODEL=qwen2.5:1.5b-instruct \
python -m pytest tests/test_v81_real_agent.py -v
```

`test_real_end_to_end_tool_call_loop` asserts a full
USER → LLM → TOOL CALL → RESULT → SECOND MODEL DECISION → ANSWER cycle and that
no raw `<tool_call>` markup reaches the user. `5 skipped` means no model was
found - not that the feature works.

---

## Troubleshooting

**`could not connect to ollama app`**
The CLI needs the host when the server is not on the default socket:
```bash
OLLAMA_HOST=127.0.0.1:11434 ollama list
```

**`llama runner process has terminated: signal: killed`**
Out of RAM. Use a smaller model (`qwen2.5:0.5b-instruct-q4_K_M`) or free memory.

**`model requires more system memory (1.7 GiB) than is available`**
Same cause, reported cleanly before loading. Drop to a smaller model.

**Mode still shows `DETERMINISTIC FALLBACK`**
Check, in order:
1. `curl localhost:11434/api/tags` responds.
2. The model in `OLLAMA_MODEL` appears in that list - otherwise `ollama pull` it.
3. `/api/provider` `detail` states exactly what is wrong.

**The answer contains literal `<tool_call>{...}</tool_call>`**
Your Ollama is too old to parse tool calls. Upgrade to 0.6.x+.

**Server fails to start extracting runners into `/tmp`**
Small `/tmp` (often a tmpfs). Point it elsewhere:
```bash
TMPDIR=~/.ollama/tmp OLLAMA_TMPDIR=~/.ollama/tmp ollama serve
```

**Responses are slow**
Expected on CPU - roughly 15 tokens/sec for a 0.5B model. Raise `LLM_TIMEOUT_S`
rather than reducing the context.
