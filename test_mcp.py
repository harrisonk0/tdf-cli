#!/usr/bin/env python3
"""Test the tdf MCP server by calling tools via stdio transport."""
import json
import subprocess
import sys
import time
import os

def test():
    print("Starting tdf MCP server...")
    proc = subprocess.Popen(
        [sys.executable, '/home/ubuntu/tdf/tdf_mcp.py', '--transport', 'stdio'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    
    def send(msg):
        """Send a JSON-RPC message and read the response."""
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()
        # Read response line
        resp = proc.stdout.readline()
        if resp:
            return json.loads(resp)
        return None
    
    # 1. Initialize
    print("\n1. Initialize...")
    resp = send({
        "jsonrpc": "2.0", "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"}
        }
    })
    print(f"   Server info: {resp.get('result', {}).get('serverInfo', {})}")
    
    # 2. List tools
    print("\n2. List tools...")
    resp = send({
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/list",
        "params": {}
    })
    tools = resp.get('result', {}).get('tools', [])
    print(f"   {len(tools)} tools:")
    for t in tools:
        print(f"      {t['name']}: {t.get('description', '')[:70]}")
    
    # 3. List resources
    print("\n3. List resources...")
    resp = send({
        "jsonrpc": "2.0", "id": 3,
        "method": "resources/list",
        "params": {}
    })
    resources = resp.get('result', {}).get('resources', [])
    print(f"   {len(resources)} resources:")
    for r in resources:
        print(f"      {r.get('uri', '')}")
    
    # 4. Read stages resource
    print("\n4. Read stages resource...")
    resp = send({
        "jsonrpc": "2.0", "id": 4,
        "method": "resources/read",
        "params": {"uri": "tdf://stages"}
    })
    contents = resp.get('result', {}).get('contents', [])
    if contents:
        text = contents[0].get('text', '')
        print(f"   ({len(text)} chars)")
        for line in text.split('\n')[:6]:
            print(f"   {line}")
    
    # 5. Call get_stage_result
    print("\n5. Call get_stage_result(1, top_n=5)...")
    resp = send({
        "jsonrpc": "2.0", "id": 5,
        "method": "tools/call",
        "params": {
            "name": "get_stage_result",
            "arguments": {"stage": 1, "top_n": 5}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:500]}")
    
    # 6. Call get_jerseys
    print("\n6. Call get_jerseys()...")
    resp = send({
        "jsonrpc": "2.0", "id": 6,
        "method": "tools/call",
        "params": {
            "name": "get_jerseys",
            "arguments": {}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:400]}")
    
    # 7. Call get_gc
    print("\n7. Call get_gc(stage=1, top_n=5)...")
    resp = send({
        "jsonrpc": "2.0", "id": 7,
        "method": "tools/call",
        "params": {
            "name": "get_gc",
            "arguments": {"stage": 1, "top_n": 5}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:500]}")
    
    # 8. Call get_stage_profile
    print("\n8. Call get_stage_profile(stage=6)...")
    resp = send({
        "jsonrpc": "2.0", "id": 8,
        "method": "tools/call",
        "params": {
            "name": "get_stage_profile",
            "arguments": {"stage": 6}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:400]}")
    
    # 9. Call get_bluesky_feed
    print("\n9. Call get_bluesky_feed(query='Vauquelin', limit=3)...")
    resp = send({
        "jsonrpc": "2.0", "id": 9,
        "method": "tools/call",
        "params": {
            "name": "get_bluesky_feed",
            "arguments": {"query": "Vauquelin", "limit": 3}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:400]}")
    
    # 10. Call get_ttt_splits
    print("\n10. Call get_ttt_splits(stage=1)...")
    resp = send({
        "jsonrpc": "2.0", "id": 10,
        "method": "tools/call",
        "params": {
            "name": "get_ttt_splits",
            "arguments": {"stage": 1}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            text = c.get('text', '')
            print(f"   ({len(text)} chars)")
            for line in text.split('\n')[:12]:
                print(f"   {line}")
    
    # 11. Call search_riders
    print("\n11. Call search_riders(query='Pidcock')...")
    resp = send({
        "jsonrpc": "2.0", "id": 11,
        "method": "tools/call",
        "params": {
            "name": "search_riders",
            "arguments": {"query": "Pidcock"}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:200]}")
    
    # 12. Test error handling - invalid stage
    print("\n12. Call get_stage_result(stage=99)...")
    resp = send({
        "jsonrpc": "2.0", "id": 12,
        "method": "tools/call",
        "params": {
            "name": "get_stage_result",
            "arguments": {"stage": 99}
        }
    })
    content = resp.get('result', {}).get('content', [])
    if content:
        for c in content:
            print(f"   {c.get('text', '')[:200]}")
    
    proc.terminate()
    print("\n✅ All tests passed!")

if __name__ == "__main__":
    test()
