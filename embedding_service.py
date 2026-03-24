import os
import sys
from typing import List
from sentence_transformers import SentenceTransformer

class EmbeddingService:
    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EmbeddingService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Ensure model is only loaded once
        if EmbeddingService._model is None:
            print("[lm-proxy:embedding_service] Loading jina-embeddings-v2-base-code...", file=sys.stderr)
            try:
                # Load the model. It will auto-download on first run.
                # trust_remote_code=True is required for Jina v2
                EmbeddingService._model = SentenceTransformer(
                    'jinaai/jina-embeddings-v2-base-code', 
                    trust_remote_code=True
                )
                print("[lm-proxy:embedding_service] Model loaded successfully.", file=sys.stderr)
            except Exception as e:
                print(f"[lm-proxy:embedding_service] ERROR loading model: {e}", file=sys.stderr)
                raise

    def embed_batch(self, texts: List[str], batch_size: int = 64) -> List[List[float]]:
        """Generate embeddings for a batch of texts."""
        if not EmbeddingService._model:
            raise RuntimeError("Embedding model not loaded")
        
        # sentence-transformers encode returns numpy arrays by default, 
        # we convert them to lists for JSON/Neo4j compatibility.
        embeddings = EmbeddingService._model.encode(
            texts, 
            batch_size=batch_size, 
            show_progress_bar=False,
            convert_to_tensor=False
        )
        return embeddings.tolist()

# Singleton accessor
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
