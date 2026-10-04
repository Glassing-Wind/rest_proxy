"""Explicit Ladybug file-outline schema and query, version 1.

This bounded schema is not the complete ts-pack graph contract.
"""
SYMBOL_LABELS = (
    'Function', 'Class', 'Struct', 'Trait', 'Enum', 'Module', 'Method',
    'Protocol', 'Interface', 'Extension', 'TypeAlias', 'AssociatedType', 'EnumCase',
)
SCHEMA_VERSION = 1
SCHEMA_STATEMENTS = (
    'CREATE NODE TABLE IF NOT EXISTS EmbeddedSchema(id STRING PRIMARY KEY, version INT64)',
    'CREATE NODE TABLE IF NOT EXISTS File(id STRING PRIMARY KEY, path STRING, project_id STRING)',
    *(f'CREATE NODE TABLE IF NOT EXISTS {label}(id STRING PRIMARY KEY, name STRING, '
      'start_line INT64, end_line INT64, signature STRING, project_id STRING)'
      for label in SYMBOL_LABELS),
    'CREATE REL TABLE IF NOT EXISTS CONTAINS(' +
    ', '.join(f'FROM File TO {label}' for label in SYMBOL_LABELS) + ')',
)
# Exact backend query; no rewriting of user Cypher or Neo4j query text.
FILE_SYMBOL_QUERY = ' UNION ALL '.join(
    f"MATCH (f:File {{id:$fid}})-[:CONTAINS]->(s:{label}) "
    f"RETURN '{label}' AS kind, s.id AS id, s.name AS name, "
    's.start_line AS start, s.end_line AS `end`, s.signature AS sig'
    for label in SYMBOL_LABELS
)

SCHEMA_COLUMNS = {
    'EmbeddedSchema': {'id': 'STRING', 'version': 'INT64'},
    'File': {'id': 'STRING', 'path': 'STRING', 'project_id': 'STRING'},
    **{label: {'id': 'STRING', 'name': 'STRING', 'start_line': 'INT64',
               'end_line': 'INT64', 'signature': 'STRING', 'project_id': 'STRING'}
       for label in SYMBOL_LABELS},
}
