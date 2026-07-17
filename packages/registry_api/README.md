# kiln-registry-api

[![PyPI](https://img.shields.io/pypi/v/kiln-registry-api.svg)](https://pypi.org/project/kiln-registry-api/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](https://github.com/aryabyte21/kiln/blob/main/LICENSE)

**Kiln** is a self-evolving tool registry for AI agents. Define a tool once
(spec + Python impl), then call it from any framework — AG2, LangChain,
Pydantic AI, Mistral, OpenAI tools, MCP, or plain HTTP. When a tool you need
doesn't exist, the synthesis pipeline writes it for you and hot-loads it.

This package ships:

- The Kiln **registry SDK** — load/register/compile tools to your chosen framework.
- The Kiln **HTTP server** — production-ready FastAPI app the [`server`] extra unlocks.

## Install

SDK only (a few small deps):

```bash
pip install kiln-registry-api
```

Plus a target framework — install just what you need:

```bash
pip install "kiln-registry-api[langchain]"     # LangChain StructuredTool
pip install "kiln-registry-api[ag2]"           # AG2 / AutoGen
pip install "kiln-registry-api[pydantic_ai]"   # Pydantic AI Tool
pip install "kiln-registry-api[mistral]"       # Mistral function schema
pip install "kiln-registry-api[server]"        # Run the FastAPI registry yourself
pip install "kiln-registry-api[all]"           # Everything
```

## Use a Kiln tool from your agent

```python
from kiln_registry.runtime import KilnRuntime

runtime = KilnRuntime(target="langchain")
weather = runtime.get("com.kiln.tools.weather_forecast")  # native StructuredTool

# Plug it into any LangChain agent:
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_openai import ChatOpenAI
agent = create_tool_calling_agent(ChatOpenAI(model="gpt-4o-mini"), [weather], prompt)
AgentExecutor(agent=agent, tools=[weather]).invoke({"input": "Will it rain in Singapore today?"})
```

The same one-liner works for AG2, Pydantic AI, and Mistral by switching the
`target=` argument. Or skip the SDK entirely and POST to `/tools/{id}/execute`
from any language.

## Or call any tool over plain HTTP

```bash
curl -X POST https://your-kiln-host/tools/com.kiln.tools.weather_forecast/execute \
  -H 'Authorization: Bearer YOUR_KILN_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"args": {"location": "Singapore"}}'
```

## Get the integration snippet for any tool

The registry exposes a `GET /tools/{id}/integrations` endpoint that returns a
copy-pasteable code block for every supported framework — plus the JSON Schema
for the tool's arguments. The Kiln UI uses this to power the **Integrations**
tab on every tool page.

## Run the registry yourself

```bash
pip install "kiln-registry-api[server]"
uvicorn kiln_registry.main:app --host 0.0.0.0 --port 8766
```

By default the registry loads every tool found under `./registry/tools/`. Drop
in a `spec.yaml` + impl file and they appear on the next process start.

## Repository

Source, examples, the synthesis pipeline, the chat console, and the MCP bridge
all live in the monorepo at <https://github.com/aryabyte21/kiln>.

## License

Apache 2.0.
