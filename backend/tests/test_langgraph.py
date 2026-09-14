"""The agent must be a genuine compiled LangGraph StateGraph."""
from langgraph.graph.state import CompiledStateGraph


def test_graph_is_real_langgraph(runtime):
    graph = runtime.agent.build()
    assert isinstance(graph, CompiledStateGraph)
    nodes = set(graph.get_graph().nodes)
    for expected in ("load_context", "agent", "tools", "memory_manager"):
        assert expected in nodes, f"missing node {expected}"


def test_graph_executes_and_returns_answer(runtime, user):
    result = runtime.agent.run(user, "graph-thread-1", "Hello there.")
    assert result["answer"].strip()
    assert result["provider"] in {"demo", "openai", "ollama"}
    assert any(a["type"] == "LOAD_CONTEXT" for a in result["activity"])


def test_conditional_edge_routes_to_memory_manager(runtime, user):
    result = runtime.agent.run(user, "graph-thread-2",
                               "I prefer short answers with code samples.")
    assert result["answer"]
    kinds = [a["type"] for a in result["activity"]]
    assert "DEMO_PLANNER" in kinds or "MODEL_CALL" in kinds
