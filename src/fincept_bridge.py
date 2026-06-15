"""
Fincept ↔ memory-core-mcp bridge.

Provides a thin client for Fincept agents to read/write
persistent memory through the memory-core-mcp HTTP gateway.

Usage:
    from src.fincept_bridge import MemoryBridge
    
    bridge = MemoryBridge(base_url="http://localhost:7700", token="...")
    bridge.store(agent="research_agent", content="Momentum factor showed decay...", tags=["momentum"])
    results = bridge.recall(query="momentum factor decay", top_k=5)
"""
from typing import Any, Dict, List, Optional
import urllib.request
import json
import os


class MemoryBridge:
    """HTTP client for the memory-core-mcp Oracle ADB gateway."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None
    ):
        self.base_url = base_url or os.getenv("MEMORY_CORE_URL", "http://localhost:7700")
        self.token = token or os.getenv("MEMORY_CORE_TOKEN", "")

    def _req(self, method: str, path: str, data: Optional[Dict] = None) -> Dict:
        url = f"{self.base_url}{path}"
        payload = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=payload, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def store(self, agent: str, content: str, tags: List[str] = None) -> Dict[str, Any]:
        """Store a memory fragment with optional tags."""
        return self._req("POST", "/api/memory", {
            "agent": agent,
            "content": content,
            "tags": tags or [],
            "source": "fincept-ai-ops"
        })

    def recall(self, query: str, top_k: int = 5, agent: Optional[str] = None) -> List[Dict]:
        """Semantic recall via Oracle AI Vector Search."""
        params = f"?q={urllib.parse.quote(query)}&top_k={top_k}"
        if agent:
            params += f"&agent={agent}"
        return self._req("GET", f"/api/memory/search{params}")

    def list_recent(self, limit: int = 20) -> List[Dict]:
        """List most recent memory entries."""
        return self._req("GET", f"/api/memory?limit={limit}&order=desc")
