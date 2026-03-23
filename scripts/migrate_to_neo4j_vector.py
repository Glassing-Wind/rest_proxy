import asyncio
import os
import sys
import psycopg
import json
from typing import List

# Add the project root to sys.path for internal imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import memory_store
import graph_bootstrap

async def migrate():
    print("Starting migration from Postgres to Neo4j Vector...")
    
    # 1. Initialize databases
    await memory_store.open_pool()
    await graph_bootstrap.init_graph_db()
    
    # 2. Extract from Postgres
    pg_dsn = os.getenv("LM_PROXY_PG_DSN", "postgresql:///lm_proxy_memory")
    try:
        async with await psycopg.AsyncConnection.connect(pg_dsn) as conn:
            # Use a named cursor for server-side batching
            async with conn.cursor(name="migration_cursor") as cur:
                print("Fetching embeddings from Postgres in batches...", flush=True)
                await cur.execute("SELECT project_id, file_path, chunk_index, content, embedding FROM codebase_embeddings")
                
                # Batch processing for Neo4j performance
                BATCH_SIZE = 100
                driver = graph_bootstrap.get_driver()
                
                async def process_batch(batch):
                    cypher = """
                    UNWIND $batch as row
                    MERGE (chk:Chunk {id: row.chk_id})
                    SET chk.project_id = row.pid,
                        chk.file_path = row.path,
                        chk.chunk_index = row.idx,
                        chk.text = row.text,
                        chk.embedding = row.vec
                    WITH chk, row
                    OPTIONAL MATCH (f:File {id: row.fid})
                    FOREACH (ignore IN CASE WHEN f IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (f)-[:HAS_CHUNK]->(chk)
                    )
                    """
                    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                        await session.run(cypher, batch=batch)

                current_batch = []
                total_count = 0
                
                while True:
                    rows = await cur.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                        
                    batch_data = []
                    for row in rows:
                        project_id, file_path, chunk_index, content, embedding_str = row
                        
                        if isinstance(embedding_str, str):
                            embedding = json.loads(embedding_str)
                        else:
                            embedding = embedding_str
                            
                        batch_data.append({
                            "chk_id": f"{project_id}:{file_path}:{chunk_index}",
                            "pid": project_id,
                            "path": file_path,
                            "idx": chunk_index,
                            "text": content,
                            "vec": embedding,
                            "fid": f"{project_id}:file:{file_path}"
                        })
                    
                    await process_batch(batch_data)
                    total_count += len(batch_data)
                    if total_count % 500 == 0:
                        print(f"Migrated {total_count} chunks...", flush=True)
                
                print(f"Migration complete! Successful: {total_count}", flush=True)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Migration failed: {str(e)}")
    finally:
        await graph_bootstrap.close_graph_db()

if __name__ == "__main__":
    asyncio.run(migrate())
