# Neo4j CLI with the existing Desktop graph

Verified October 16, 2026: Neo4j Desktop 2.3.0, neo4j-cli v1.17.0, local instance
`repo` (Neo4j 2026.02.2), database `proxy`, URI `neo4j://127.0.0.1:7687`.
The Desktop update did not upgrade the database engine.

Desktop's existing secure credential storage supplies the CLI connection at runtime.
No separate CLI password profile or Aura credentials are needed for this local graph.
Desktop and the `repo` instance must be running. The `desktop` selector requires a
single running local instance; it fails rather than guessing among multiple instances.
Always select `--database proxy` explicitly for this project's repository graph.

Inspect Desktop and discover the schema before composing Cypher:

```sh
neo4j-cli desktop doctor --format toon
neo4j-cli desktop list --format toon
neo4j-cli query :schema --credential desktop --database proxy --format toon
```

A bounded read-only connection check:

```sh
neo4j-cli query 'MATCH (n) RETURN count(n) AS node_count' \
  --credential desktop --database proxy --format toon
```

Run from a directory without a conflicting `.env`; explicit credential selection
does not make unrelated connection overrides desirable. No password needs to be
copied into commands, logs, project configuration or chat. The observed graph had
1,099,553 nodes, 12,778,888 relationships, 20 indexes and 11 constraints at setup.
These are a dated connection check, not a freshness or retrieval-quality audit.

The `repo` instance was stopped after the Desktop update and was started to verify
this setup. No graph records, indexes, users, roles or engine settings were changed.
The existing graph supplied the example; no sample dataset or new cloud instance
was created. Starting/stopping instances and data writes remain separate explicit
operations; normal source investigations use read-only queries.
