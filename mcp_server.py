import asyncio
import os
import json
from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import FastMCP

# Import existing functionality from rest_proxy
import memory_store
import memory_retrieval
import memory_summary
import skeleton_extractor
import proxy

# Initialize FastMCP server
mcp = FastMCP("rest_proxy")

@mcp.tool()
async def search_memory(session_id: str, query: str) -> str:
    """
    Search for context in the memory store for a given session.
    
    Args:
        session_id: The unique identifier for the session/project.
        query: The search query or current user message.
    """
    try:
        # Ensure pool is open if using Postgres/Redis
        if memory_store._MEMORY_ENABLE_PERSISTENCE:
            await memory_store.open_pool()
            
        assembled = await memory_retrieval.assemble_memory(session_id, query)
        if assembled and assembled.assembled_text:
            return assembled.assembled_text
        return "No relevant memory found for this session."
    except Exception as e:
        return f"Error searching memory: {str(e)}"

@mcp.tool()
async def get_session_summary(session_id: str) -> str:
    """
    Get the current rolling summary for a session.
    
    Args:
        session_id: The unique identifier for the session.
    """
    try:
        summary = await memory_store.get_rolling_summary(session_id)
        if summary:
            return summary
        return "No summary available for this session."
    except Exception as e:
        return f"Error retrieving summary: {str(e)}"

@mcp.tool()
async def code_skeleton(file_path: str) -> str:
    """
    Extract the structural skeleton (classes, functions, etc.) from a source file.
    Supports .py, .swift, .js, .ts, .jsx, .tsx.
    
    Args:
        file_path: Absolute path to the source file.
    """
    try:
        if not os.path.exists(file_path):
            return f"File not found: {file_path}"
            
        with open(file_path, "r") as f:
            code = f.read()
            
        skeleton = skeleton_extractor.extract_skeleton(code, file_path)
        if skeleton:
            return skeleton
        return "Could not extract skeleton (unsupported file type or empty file)."
    except Exception as e:
        return f"Error extracting skeleton: {str(e)}"

@mcp.tool()
async def list_available_models() -> str:
    """
    List models currently available in LM Studio via the proxy.
    """
    try:
        models_data = await proxy.fetch_lmstudio_models()
        keys = proxy.extract_model_keys(models_data)
        if keys:
            return "\n".join(keys)
        return "No models found."
    except Exception as e:
        return f"Error listing models: {str(e)}"

if __name__ == "__main__":
    # Run the server using stdio by default
    mcp.run()
