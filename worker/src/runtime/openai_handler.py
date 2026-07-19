"""Stub for coze_coding_utils.openai.handler.OpenAIChatHandler."""

import logging

logger = logging.getLogger(__name__)


class OpenAIChatHandler:
    """Minimal OpenAI chat handler. Kept for API compatibility."""
    
    def __init__(self, *args, **kwargs):
        pass
    
    async def chat(self, *args, **kwargs):
        raise NotImplementedError("OpenAIChatHandler.chat not implemented in standalone mode")
