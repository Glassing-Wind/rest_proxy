import asyncio
import os
import time
import json
from typing import List, Dict, Any
import psycopg
from neo4j import AsyncGraphDatabase
from dotenv import load_dotenv

load_dotenv()

PG_DSN = os.getenv("LM_PROXY_PG_DSN")
NEO4J_URI = os.getenv("LM_PROXY_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("LM_PROXY_NEO4J_PASSWORD")
NEO4J_DB = os.getenv("LM_PROXY_NEO4J_DB", "neo4j")

async def migrate_memory():
    if not PG_DSN:
        print("PG_DSN not found. Skipping.")
        return

    print(f"Connecting to Postgres for memory migration...")
    pg_conn = await psycopg.AsyncConnection.connect(PG_DSN)
    
    print(f"Connecting to Neo4j...")
    neo4j_driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    try:
        # 1. Migrate Conversation Turns
        async with pg_conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM conversation_turns")
            count = (await cur.fetchone())[0]
            print(f"Found {count} conversation turns in Postgres.")
            
            await cur.execute("SELECT id, session_id, turn_index, role, content, compact_content, model, tool_name, tool_call_id, metadata, created_at FROM conversation_turns")
            
            batch = []
            migrated = 0
            async for row in cur:
                batch.append({
                    "id": str(row[0]),
                    "session_id": str(row[1]),
                    "turn_index": row[2],
                    "role": row[3],
                    "content": row[4],
                    "compact_content": row[5],
                    "model": row[6],
                    "tool_name": row[7],
                    "tool_call_id": row[8],
                    "metadata": json.dumps(row[9] if isinstance(row[9], dict) else {}),
                    "created_at": row[10].timestamp() if hasattr(row[10], 'timestamp') else time.time()
                })
                
                if len(batch) >= 100:
                    await insert_turns_neo4j(neo4j_driver, batch)
                    migrated += len(batch)
                    print(f"Migrated {migrated}/{count} turns...")
                    batch = []
            
            if batch:
                await insert_turns_neo4j(neo4j_driver, batch)
                migrated += len(batch)
                print(f"Final: Migrated {migrated} turns.")

        # 2. Migrate Memory Embeddings (Durable Facts)
        async with pg_conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM memory_embeddings")
            count = (await cur.fetchone())[0]
            print(f"Found {count} memory embeddings in Postgres.")
            
            await cur.execute("SELECT id, session_id, ref_id, ref_type, compact_text, embedding, metadata, created_at FROM memory_embeddings")
            
            batch = []
            migrated = 0
            async for row in cur:
                # Convert vector string to list
                vec = row[5]
                if isinstance(vec, str):
                    vec = [float(v) for v in vec.strip('[]').split(',')]
                
                batch.append({
                    "id": str(row[0]),
                    "session_id": str(row[1]),
                    "ref_id": str(row[2]),
                    "ref_type": row[3],
                    "text": row[4],
                    "vector": vec,
                    "metadata": json.dumps(row[6] if isinstance(row[6], dict) else {}),
                    "created_at": row[7].timestamp() if hasattr(row[7], 'timestamp') else time.time()
                })
                
                if len(batch) >= 100:
                    await insert_memories_neo4j(neo4j_driver, batch)
                    migrated += len(batch)
                    print(f"Migrated {migrated}/{count} memories...")
                    batch = []
            
            if batch:
                await insert_memories_neo4j(neo4j_driver, batch)
                migrated += len(batch)
                print(f"Final: Migrated {migrated} memories.")

    finally:
        await pg_conn.close()
        await neo4j_driver.close()

async def insert_turns_neo4j(driver, batch):
    cypher = """
    UNWIND $batch AS data
    MERGE (t:MemoryTurn {id: data.id})
    SET t.session_id = data.session_id,
        t.turn_index = data.turn_index,
        t.role = data.role,
        t.content = data.content,
        t.compact_content = data.compact_content,
        t.model = data.model,
        t.tool_name = data.tool_name,
        t.tool_call_id = data.tool_call_id,
        t.metadata = data.metadata,
        t.created_at = data.created_at
    WITH t, data
    MATCH (s:Session {id: data.session_id})
    MERGE (s)-[:HAS_TURN]->(t)
    """
    async with driver.session(database=NEO4J_DB) as session:
        await session.run(cypher, batch=batch)

async def insert_memories_neo4j(driver, batch):
    cypher = """
    UNWIND $batch AS data
    MERGE (m:MemoryEmbedding {id: data.id})
    SET m.session_id = data.session_id,
        m.ref_id = data.ref_id,
        m.ref_type = data.ref_type,
        m.text = data.text,
        m.vector = data.vector,
        m.metadata = data.metadata,
        m.created_at = data.created_at
    """
    async with driver.session(database=NEO4J_DB) as session:
        await session.run(cypher, batch=batch)

if __name__ == "__main__":
    asyncio.run(migrate_memory())
