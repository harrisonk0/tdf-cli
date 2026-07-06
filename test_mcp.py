#!/usr/bin/env python3
"""Tests for the tdf MCP server via stdio transport."""
import json
import subprocess
import sys
import os
import time
import pytest

SERVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tdf_mcp.py")

@pytest.fixture(scope="module")
def mcp_server():
    """Start the MCP server subprocess and yield a send function."""
    proc = subprocess.Popen(
        [sys.executable, SERVER_PATH, "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def send(msg, timeout=10):
        """Send a JSON-RPC message and read the response with timeout."""
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()
        # Simple timeout: poll stdout
        start = time.time()
        while time.time() - start < timeout:
            line = proc.stdout.readline()
            if line:
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
            time.sleep(0.01)
        pytest.fail(f"No response within {timeout}s for: {msg.get('method', msg)}")

    yield send

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

def test_initialize(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"}
        }
    })
    assert resp is not None, "No response to initialize"
    assert "result" in resp, f"Initialize returned error: {resp.get('error')}"
    server_info = resp["result"].get("serverInfo", {})
    assert "name" in server_info, f"Missing serverInfo.name: {server_info}"

def test_list_tools(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/list",
        "params": {}
    })
    assert resp is not None
    assert "result" in resp
    tools = resp["result"].get("tools", [])
    assert len(tools) >= 15, f"Expected >=15 tools, got {len(tools)}"
    tool_names = [t["name"] for t in tools]
    assert "get_stage_result" in tool_names
    assert "get_gc" in tool_names
    assert "get_jerseys" in tool_names
    assert "get_live_state" in tool_names

def test_list_resources(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 3,
        "method": "resources/list",
        "params": {}
    })
    assert resp is not None
    assert "result" in resp
    resources = resp["result"].get("resources", [])
    assert len(resources) >= 1
    uris = [r.get("uri", "") for r in resources]
    assert "tdf://stages" in uris

def test_read_stages_resource(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 4,
        "method": "resources/read",
        "params": {"uri": "tdf://stages"}
    })
    assert resp is not None
    assert "result" in resp
    contents = resp["result"].get("contents", [])
    assert len(contents) >= 1
    text = contents[0].get("text", "")
    assert len(text) > 100, f"Stages resource too short: {len(text)} chars"
    assert "Tour de France" in text

def test_get_stage_result(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 5,
        "method": "tools/call",
        "params": {"name": "get_stage_result", "arguments": {"stage": 1, "top_n": 5}}
    })
    assert resp is not None
    assert "result" in resp
    content = resp["result"].get("content", [])
    assert len(content) >= 1
    text = content[0].get("text", "")
    # Either returns stage data or "hasn't happened yet" - both are valid responses
    assert "Stage" in text, f"Unexpected response: {text[:200]}"

def test_get_jerseys(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 6,
        "method": "tools/call",
        "params": {"name": "get_jerseys", "arguments": {}}
    })
    assert resp is not None
    assert "result" in resp
    content = resp["result"].get("content", [])
    assert len(content) >= 1
    # Either returns jersey data or "No jersey data" - both valid
    text = content[0].get("text", "")
    assert len(text) > 0

def test_get_gc(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 7,
        "method": "tools/call",
        "params": {"name": "get_gc", "arguments": {"stage": 1, "top_n": 5}}
    })
    assert resp is not None
    assert "result" in resp
    content = resp["result"].get("content", [])
    assert len(content) >= 1
    text = content[0].get("text", "")
    assert "Classification" in text or "No GC" in text

def test_invalid_stage(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 8,
        "method": "tools/call",
        "params": {"name": "get_stage_result", "arguments": {"stage": 99}}
    })
    assert resp is not None
    assert "result" in resp
    content = resp["result"].get("content", [])
    assert len(content) >= 1
    text = content[0].get("text", "")
    assert "Stage 99" in text

def test_search_riders(mcp_server):
    send = mcp_server
    resp = send({
        "jsonrpc": "2.0", "id": 9,
        "method": "tools/call",
        "params": {"name": "search_riders", "arguments": {"query": "Pogacar"}}
    })
    assert resp is not None
    assert "result" in resp
    content = resp["result"].get("content", [])
    assert len(content) >= 1
    text = content[0].get("text", "")
    # Either finds Pogacar or not - both are valid. Just check it returns something
    assert len(text) > 0
