import asyncio
import os
from dotenv import load_dotenv

load_dotenv("/Users/michaelmarler/Projects/rest_proxy/.env")
os.environ["LM_PROXY_DEBUG"] = "true"

import graph_bootstrap

async def test_connection():
    try:
        print("Initializing graph db connection...")
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        if driver:
            print("Successfully connected to Neo4j database!")
        else:
            print("Failed to get Neo4j driver.")
    finally:
        await graph_bootstrap.close_graph_db()

if __name__ == "__main__":
    asyncio.run(test_connection())
