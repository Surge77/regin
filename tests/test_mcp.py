"""MCP tool adaptation. The SDK is not required — the session is faked."""

from __future__ import annotations

from typing import Any

from regin.tools.base import ToolRegistry
from regin.tools.mcp import MCPTool, mcp_args_model, register_mcp_tools


class FakeBlock:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class FakeResult:
    def __init__(self, content: list[FakeBlock], is_error: bool = False) -> None:
        self.content = content
        self.isError = is_error


class FakeToolSpec:
    def __init__(self, name: str, description: str, schema: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.inputSchema = schema


class FakeListing:
    def __init__(self, tools: list[FakeToolSpec]) -> None:
        self.tools = tools


class FakeSession:
    def __init__(self, tools: list[FakeToolSpec], result: FakeResult | None = None) -> None:
        self._tools = tools
        self._result = result or FakeResult([FakeBlock("text", "remote output")])
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> FakeListing:
        return FakeListing(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeResult:
        self.calls.append((name, arguments))
        return self._result


SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


def test_the_servers_schema_is_passed_through_verbatim() -> None:
    """The server is the authority on its own schema; re-deriving it invites drift."""
    model = mcp_args_model("search", SCHEMA)
    assert model.model_json_schema() == SCHEMA


def test_tool_names_are_namespaced() -> None:
    """A remote tool silently shadowing the local `bash` would be a capability escalation."""
    tool = MCPTool(FakeSession([]), "bash", "remote bash", SCHEMA)
    assert tool.name == "mcp__bash"


async def test_calling_a_tool_forwards_the_arguments() -> None:
    session = FakeSession([])
    tool = MCPTool(session, "search", "Search things", SCHEMA)
    args = tool.args_model.model_validate({"query": "parser"})

    result = await tool.run(args)

    assert session.calls == [("search", {"query": "parser"})]
    assert result == "remote output"


async def test_non_text_content_is_described_rather_than_dropped() -> None:
    """The model needs to know something came back that it cannot see."""
    session = FakeSession([], FakeResult([FakeBlock("image"), FakeBlock("text", "caption")]))
    tool = MCPTool(session, "screenshot", "Take one", SCHEMA)

    result = await tool.run(tool.args_model.model_validate({}))

    assert "[image content omitted]" in result
    assert "caption" in result


async def test_an_error_result_is_marked() -> None:
    session = FakeSession([], FakeResult([FakeBlock("text", "no such repo")], is_error=True))
    tool = MCPTool(session, "fetch", "Fetch", SCHEMA)

    assert (await tool.run(tool.args_model.model_validate({}))).startswith("error:")


async def test_an_empty_result_still_returns_something() -> None:
    """An empty tool_result body is rejected by the provider."""
    session = FakeSession([], FakeResult([]))
    tool = MCPTool(session, "noop", "Does nothing", SCHEMA)

    assert await tool.run(tool.args_model.model_validate({})) == "(no output)"


async def test_registers_every_advertised_tool() -> None:
    session = FakeSession(
        [
            FakeToolSpec("search", "Search the docs", SCHEMA),
            FakeToolSpec("fetch", "Fetch a page", SCHEMA),
        ]
    )
    registry = ToolRegistry()

    added = await register_mcp_tools(registry, session)

    assert added == ["mcp__search", "mcp__fetch"]
    assert registry.names() == ["mcp__fetch", "mcp__search"]


async def test_registered_tools_expose_the_server_schema_to_the_provider() -> None:
    """The registry's one-source-of-truth property must survive the remote boundary."""
    session = FakeSession([FakeToolSpec("search", "Search", SCHEMA)])
    registry = ToolRegistry()
    await register_mcp_tools(registry, session)

    schema = registry.schemas()[0]
    assert schema["name"] == "mcp__search"
    assert schema["input_schema"] == SCHEMA


async def test_a_tool_without_a_description_still_gets_one() -> None:
    session = FakeSession([FakeToolSpec("thing", "", SCHEMA)])
    registry = ToolRegistry()
    await register_mcp_tools(registry, session)

    assert "thing" in registry.get("mcp__thing").description
