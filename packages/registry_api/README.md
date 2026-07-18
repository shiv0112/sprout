# sprout-registry-api

[![PyPI](https://img.shields.io/pypi/v/sprout-registry-api.svg)](https://pypi.org/project/sprout-registry-api/)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue.svg)](https://github.com/aryabyte21/sprout/blob/main/LICENSE)

**Sprout** is a self-evolving tool registry for AI agents. Define a tool once
(spec + Python impl), then call it from any framework — AG2, LangChain,
Pydantic AI, Mistral, OpenAI tools, MCP, or plain HTTP. When a tool you need
doesn't exist, the synthesis pipeline writes it for you and hot-loads it.

This package ships:

- The Sprout **registry SDK** — load/register/compile tools to your chosen framework.
- The Sprout **HTTP server** — production-ready FastAPI app the [`server`] extra unlocks.

## Install

SDK only (a few small deps):

```bash
pip install sprout-registry-api
```

Plus a target framework — install just what you need:

```bash
pip install "sprout-registry-api[langchain]"     # LangChain StructuredTool
pip install "sprout-registry-api[ag2]"           # AG2 / AutoGen
pip install "sprout-registry-api[pydantic_ai]"   # Pydantic AI Tool
pip install "sprout-registry-api[mistral]"       # Mistral function schema
pip install "sprout-registry-api[server]"        # Run the FastAPI registry yourself
pip install "sprout-registry-api[all]"           # Everything
```

## Use a Sprout tool from your agent

```python
from sprout_registry.runtime import SproutRuntime

runtime = SproutRuntime(target="langchain")
weather = runtime.get("com.sprout.tools.weather_forecast")  # native StructuredTool

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
curl -X POST https://your-sprout-host/tools/com.sprout.tools.weather_forecast/execute \
  -H 'Authorization: Bearer YOUR_SPROUT_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"args": {"location": "Singapore"}}'
```

## Get the integration snippet for any tool

The registry exposes a `GET /tools/{id}/integrations` endpoint that returns a
copy-pasteable code block for every supported framework — plus the JSON Schema
for the tool's arguments. The Sprout UI uses this to power the **Integrations**
tab on every tool page.

## Run the registry yourself

```bash
pip install "sprout-registry-api[server]"
uvicorn sprout_registry.main:app --host 0.0.0.0 --port 8766
```

By default the registry loads every tool found under `./registry/tools/`. Drop
in a `spec.yaml` + impl file and they appear on the next process start.

## Repository

Source, examples, the synthesis pipeline, the chat console, and the MCP bridge
all live in the monorepo at <https://github.com/aryabyte21/sprout>.

## License

Apache 2.0.
