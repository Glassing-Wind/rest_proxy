"""Storage selection without importing engines or opening external services."""
import os

EMBEDDED_INDEXING_UNAVAILABLE = (
    'ERROR: Embedded indexing is not implemented yet. Ladybug currently supports '
    'experimental file-outline reads; the existing workers write to Neo4j/Postgres. '
    'No indexing worker was started.'
)


def embedded_graph_selected() -> bool:
    return (os.getenv('LM_PROXY_STORAGE_BACKEND', '').strip().lower() == 'embedded'
            or os.getenv('LM_PROXY_GRAPH_BACKEND', '').strip().lower() in {'ladybug', 'kuzu'})
