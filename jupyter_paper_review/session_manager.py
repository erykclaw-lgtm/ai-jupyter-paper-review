"""Session manager — thin wrapper around ClaudeBridge session methods.

Provides the same interface but can be extended with caching,
indexing, or database backing later.
"""

from .claude_bridge import ClaudeBridge


class SessionManager:
    """Manages paper review sessions."""

    def __init__(self, bridge: ClaudeBridge):
        self.bridge = bridge

    def create(self, paper_url: str | None = None, model: str = "claude-sonnet-4-6"):
        return self.bridge.create_session(paper_url=paper_url, model=model)

    def get(self, session_id: str):
        return self.bridge.get_session(session_id)

    def list_all(self):
        return self.bridge.list_sessions()

    async def delete(self, session_id: str):
        return await self.bridge.delete_session(session_id)
