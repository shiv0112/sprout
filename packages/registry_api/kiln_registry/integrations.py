"""
kiln_registry/integrations.py
─────────────────────────────
Generate copy-pasteable code snippets that embed any registered Kiln tool
into a third-party agent framework. Snippet generation is *purely textual*
— it does not import the target framework, so this works in slim images
where only `kiln-registry-api` (no extras) is installed.

Returned shape per target:
    {
        "target":            "langchain",
        "label":             "LangChain",
        "install":           'pip install "kiln-registry-api[langchain]"',
        "language":          "python",
        "snippet":           "<copy-pasteable code>",
        "schema":            { ... JSON Schema for the tool's args ... },
    }
"""

from __future__ import annotations

import json
from typing import Any

from kiln_shared.spec import KilnTool

_JSON_TYPE = {
    "str": "string", "int": "integer", "float": "number",
    "bool": "boolean", "list": "array", "dict": "object",
}


def _arg_schema(tool: KilnTool) -> dict[str, Any]:
    """Build a JSON-Schema fragment describing the tool's call args."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in tool.spec.params:
        prop: dict[str, Any] = {
            "type": _JSON_TYPE.get(p.type, "string"),
            "description": p.description or "",
        }
        if p.enum:
            prop["enum"] = list(p.enum)
        if p.default is not None:
            prop["default"] = p.default
        properties[p.name] = prop
        if p.required:
            required.append(p.name)
    return {"type": "object", "properties": properties, "required": required}


def _example_args(tool: KilnTool) -> dict[str, Any]:
    """Pick a plausible example payload from the spec's required params."""
    out: dict[str, Any] = {}
    for p in tool.spec.params:
        if p.default is not None:
            out[p.name] = p.default
        elif p.enum:
            out[p.name] = p.enum[0]
        elif p.type == "str":
            out[p.name] = "..."
        elif p.type == "int":
            out[p.name] = 0
        elif p.type == "float":
            out[p.name] = 0.0
        elif p.type == "bool":
            out[p.name] = False
        elif p.type == "list":
            out[p.name] = []
        elif p.type == "dict":
            out[p.name] = {}
    return out


# ── Per-target snippet builders ─────────────────────────────────────────────

def _curl(tool: KilnTool, registry_url: str) -> str:
    args = json.dumps({"args": _example_args(tool)}, indent=2)
    # Single-quoted bash strings don't support backslash-escaping, so embed
    # an apostrophe by closing the quote, emitting a literal `'\''`, and
    # reopening. `'` → `'\''`. This is the standard shell idiom for
    # quoting arbitrary text safely.
    args_escaped = args.replace("'", "'\\''")
    return (
        f"curl -X POST '{registry_url}/tools/{tool.id}/execute' \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -H 'Authorization: Bearer YOUR_KILN_API_KEY' \\\n"
        f"  -d '{args_escaped}'"
    )


def _python_requests(tool: KilnTool, registry_url: str) -> str:
    args = json.dumps(_example_args(tool), indent=4).replace("\n", "\n    ")
    return (
        "import os\n"
        "import requests\n\n"
        f'response = requests.post(\n'
        f'    "{registry_url}/tools/{tool.id}/execute",\n'
        f'    headers={{"Authorization": f"Bearer {{os.environ[\'KILN_API_KEY\']}}"}},\n'
        f'    json={{"args": {args}}},\n'
        f'    timeout=30,\n'
        f")\n"
        "result = response.json()['result']\n"
        "print(result)"
    )


def _ag2(tool: KilnTool, registry_url: str) -> str:
    return (
        "# Requires:  pip install \"kiln-registry-api[ag2]\"\n"
        "from kiln_registry.runtime import KilnRuntime\n"
        "from autogen import AssistantAgent, UserProxyAgent\n\n"
        f'runtime = KilnRuntime(target="ag2")\n'
        f'tool = runtime.get("{tool.id}")\n\n'
        "assistant = AssistantAgent(name=\"assistant\", llm_config={\"config_list\": [...]})\n"
        "executor = UserProxyAgent(name=\"executor\", code_execution_config=False)\n\n"
        "tool.register(caller=assistant, executor=executor)\n\n"
        f'executor.initiate_chat(assistant, message="Use the {tool.spec.name} tool to ...")'
    )


def _langchain(tool: KilnTool, registry_url: str) -> str:
    return (
        "# Requires:  pip install \"kiln-registry-api[langchain]\"\n"
        "from kiln_registry.runtime import KilnRuntime\n"
        "from langchain.agents import AgentExecutor, create_tool_calling_agent\n"
        "from langchain_openai import ChatOpenAI\n"
        "from langchain_core.prompts import ChatPromptTemplate\n\n"
        f'runtime = KilnRuntime(target="langchain")\n'
        f'tool = runtime.get("{tool.id}")  # native LangChain StructuredTool\n\n'
        "llm = ChatOpenAI(model=\"gpt-4o-mini\")\n"
        "prompt = ChatPromptTemplate.from_messages([\n"
        "    (\"system\", \"You are a helpful assistant.\"),\n"
        "    (\"human\", \"{input}\"),\n"
        "    (\"placeholder\", \"{agent_scratchpad}\"),\n"
        "])\n"
        "agent = create_tool_calling_agent(llm, [tool], prompt)\n"
        "executor = AgentExecutor(agent=agent, tools=[tool])\n\n"
        f'executor.invoke({{"input": "Use {tool.spec.name} to ..."}})'
    )


def _pydantic_ai(tool: KilnTool, registry_url: str) -> str:
    return (
        "# Requires:  pip install \"kiln-registry-api[pydantic_ai]\"\n"
        "from kiln_registry.runtime import KilnRuntime\n"
        "from pydantic_ai import Agent\n\n"
        f'runtime = KilnRuntime(target="pydantic_ai")\n'
        f'tool = runtime.get("{tool.id}")\n\n'
        f'agent = Agent("openai:gpt-4o-mini", tools=[tool.as_tool()])\n'
        f'result = agent.run_sync("Use {tool.spec.name} to ...")\n'
        "print(result.data)"
    )


def _mistral(tool: KilnTool, registry_url: str) -> str:
    schema = _arg_schema(tool)
    schema_text = json.dumps(schema, indent=2).replace("\n", "\n    ")
    return (
        "# Requires:  pip install mistralai\n"
        "import os\n"
        "from mistralai import Mistral\n\n"
        "client = Mistral(api_key=os.environ[\"MISTRAL_API_KEY\"])\n\n"
        "tools = [{\n"
        '    "type": "function",\n'
        "    \"function\": {\n"
        f'        "name": "{tool.spec.name}",\n'
        f'        "description": {json.dumps(tool.spec.description)},\n'
        f'        "parameters": {schema_text},\n'
        "    },\n"
        "}]\n\n"
        "response = client.chat.complete(\n"
        '    model="mistral-large-latest",\n'
        f'    messages=[{{"role": "user", "content": "Use {tool.spec.name} to ..."}}],\n'
        "    tools=tools,\n"
        "    tool_choice=\"auto\",\n"
        ")\n"
        "# When the model picks the tool, POST its arguments to:\n"
        f"#   {registry_url}/tools/{tool.id}/execute\n"
        "# and feed the result back in the next turn."
    )


def _openai(tool: KilnTool, registry_url: str) -> str:
    schema = _arg_schema(tool)
    schema_text = json.dumps(schema, indent=2).replace("\n", "\n    ")
    return (
        "# Works with any OpenAI-compatible client (OpenAI, Anthropic, Groq, Fireworks, etc.)\n"
        "from openai import OpenAI\n\n"
        "client = OpenAI()\n\n"
        "tools = [{\n"
        '    "type": "function",\n'
        "    \"function\": {\n"
        f'        "name": "{tool.spec.name}",\n'
        f'        "description": {json.dumps(tool.spec.description)},\n'
        f'        "parameters": {schema_text},\n'
        "    },\n"
        "}]\n\n"
        "response = client.chat.completions.create(\n"
        '    model="gpt-4o-mini",\n'
        f'    messages=[{{"role": "user", "content": "Use {tool.spec.name} to ..."}}],\n'
        "    tools=tools,\n"
        ")\n"
        "# When the model emits a tool_call, POST its arguments to:\n"
        f"#   {registry_url}/tools/{tool.id}/execute"
    )


def _mcp(tool: KilnTool, registry_url: str, mcp_url: str) -> str:
    config = {
        "mcpServers": {
            "kiln": {
                "transport": "streamable-http",
                "url": f"{mcp_url}/mcp",
                "headers": {"Authorization": "Bearer YOUR_KILN_API_KEY"},
            }
        }
    }
    return json.dumps(config, indent=2)


# ── Public assembly ─────────────────────────────────────────────────────────

def integrations_for(
    tool: KilnTool,
    registry_url: str,
    mcp_url: str | None = None,
) -> list[dict[str, Any]]:
    """Return a list of integration descriptors for a tool, ordered for UI.

    Args:
        registry_url: Public URL of the registry API (used in HTTP snippets).
        mcp_url:      Public URL of the MCP server. If omitted, falls back to
                      a sibling-port heuristic on the registry URL.
    """
    if mcp_url is None:
        # Best-effort heuristic for local dev (registry on :8766, MCP on :8768).
        # Production deployments must set mcp_url explicitly so we never emit
        # a snippet that points users at the wrong host.
        mcp_url = registry_url.rstrip("/").replace(":8766", ":8768")
    schema = _arg_schema(tool)
    return [
        {
            "target": "curl",
            "label": "cURL",
            "language": "bash",
            "install": None,
            "snippet": _curl(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "python",
            "label": "Python (HTTP)",
            "language": "python",
            "install": "pip install requests",
            "snippet": _python_requests(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "openai",
            "label": "OpenAI tools",
            "language": "python",
            "install": "pip install openai",
            "snippet": _openai(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "ag2",
            "label": "AG2 (AutoGen)",
            "language": "python",
            "install": 'pip install "kiln-registry-api[ag2]"',
            "snippet": _ag2(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "langchain",
            "label": "LangChain",
            "language": "python",
            "install": 'pip install "kiln-registry-api[langchain]" langchain langchain-openai',
            "snippet": _langchain(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "pydantic_ai",
            "label": "Pydantic AI",
            "language": "python",
            "install": 'pip install "kiln-registry-api[pydantic_ai]"',
            "snippet": _pydantic_ai(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "mistral",
            "label": "Mistral",
            "language": "python",
            "install": "pip install mistralai",
            "snippet": _mistral(tool, registry_url),
            "schema": schema,
        },
        {
            "target": "mcp",
            "label": "MCP (Claude / Cursor)",
            "language": "json",
            "install": (
                f'Add to ~/Library/Application Support/Claude/claude_desktop_config.json '
                f'(macOS) or %APPDATA%\\Claude\\claude_desktop_config.json (Windows). '
                f'Then ask: "Use the {tool.spec.name} tool to ..."'
            ),
            "snippet": _mcp(tool, registry_url, mcp_url),
            "schema": schema,
        },
    ]
