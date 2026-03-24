#!/usr/bin/env python3
"""
graph_indexer.py - Native Tree-Sitter GraphRag Indexer

Refactored to use tree-sitter-language-pack for recursive structural analysis.
Supports 170+ languages with high-fidelity symbol extraction (Classes, Traits, Impls, etc.).
"""

import os
import sys
import hashlib
import asyncio
import importlib
import subprocess
import json
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
load_dotenv()

import graph_bootstrap
from graph_bootstrap import get_driver as get_neo4j_driver

def _debug(msg: str, error: Optional[str] = None):
    if error:
        print(f"[ERROR] {msg}: {error}", file=sys.stderr)
    else:
        print(f"[DEBUG] {msg}")

class ASTGraphBuilder:
    """Original Python indexer container (still used for Python & as a result bucket)."""
    def __init__(self, file_path: str, project_id: str):
        self.file_path = file_path
        self.project_id = project_id
        self.nodes = {
            "File": [], 
            "Class": [], 
            "Function": [], 
            "Documentation": [],
            "Struct": [],
            "Interface": [],
            "Enum": [],
            "Trait": [],
            "Impl": [],
            "Module": [],
            "Namespace": [],
            "Symbol": [],
            "Import": []
        }
        self.relationships = {"CONTAINS": [], "INHERITS": [], "CALLS": [], "HAS_IMPORT": []}
        self.current_class: Optional[str] = None
        self.current_function: Optional[str] = None
        
        self.file_id = f"{project_id}:{file_path}"

    def _make_class_id(self, name: str) -> str:
        return f"{self.project_id}:class:{name}"

    def visit(self, tree):
        """Python-specific AST traversal (legacy but kept for Python context)."""
        import ast
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_id = self._make_class_id(node.name)
                self.nodes["Class"].append({
                    "id": class_id, "name": node.name, "project_id": self.project_id,
                    "filepath": self.file_path, "start_line": node.lineno
                })
                self.relationships["CONTAINS"].append({
                    "from_label": "File", "from_id": self.file_id,
                    "to_label": "Class", "to_id": class_id
                })
                # Basic inheritance
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        self.relationships["INHERITS"].append({
                            "from_label": "Class", "from_id": class_id,
                            "to_label": "Class", "to_id": self._make_class_id(base.id)
                        })
            elif isinstance(node, ast.FunctionDef):
                func_id = f"{self.project_id}:func:{self.file_path}.{node.name}"
                self.nodes["Function"].append({
                    "id": func_id, "name": node.name, "project_id": self.project_id,
                    "filepath": self.file_path, "start_line": node.lineno
                })
                container_label = "File"
                container_id = self.file_id
                # Heuristic for class methods
                # (Actual nesting would require a recursive visitor)
                self.relationships["CONTAINS"].append({
                    "from_label": container_label, "from_id": container_id,
                    "to_label": "Function", "to_id": func_id
                })

class TreeSitterGraphBuilder:
    def __init__(self, file_path: str, rel_path: str, project_id: str, lang_name: str):
        self.file_path = file_path # Absolute path for file reading
        self.rel_path = rel_path   # Relative path for ID stability
        self.project_id = project_id
        self.lang_name = lang_name
        
        self.nodes = {
            "File": [], "Class": [], "Function": [], "Documentation": [], 
            "Struct": [], "Interface": [], "Enum": [], "Trait": [], "Impl": [], 
            "Module": [], "Namespace": [], "Symbol": [], "Import": []
        }
        self.relationships = {"CONTAINS": [], "INHERITS": [], "CALLS": [], "HAS_IMPORT": []}
        
        self.file_id = f"{project_id}:{self.rel_path}"
        self.nodes["File"].append({"id": self.file_id, "path": self.rel_path, "project_id": project_id})

    def _process_item(self, item: dict, parent_id: str, parent_label: str = "File"):
        """Recursively process a high-level dictionary representation of a code symbol."""
        # Map StructureKind string to Neo4j Label
        kind_map = {
            "Class": "Class", "Struct": "Struct", "Interface": "Interface",
            "Enum": "Enum", "Trait": "Trait", "Impl": "Impl",
            "Function": "Function", "Method": "Function", "Constructor": "Function",
            "Module": "Module", "Namespace": "Namespace"
        }
        
        raw_kind = item.get("kind", "Symbol")
        label = kind_map.get(raw_kind, "Symbol")
        name = item.get("name") or f"unnamed_{raw_kind.lower()}"
        
        # Unique ID for the symbol
        symbol_id = f"{self.project_id}:{label.lower()}:{self.rel_path}:{name}"
        
        # Add Node
        span = item.get("span", {})
        start_line = span.get("start_line", 0) + 1
        
        node_data = {
            "id": symbol_id, "name": name, "project_id": self.project_id,
            "filepath": self.rel_path, "start_line": start_line
        }
        if item.get("signature"):
            node_data["signature"] = item.get("signature")
        if item.get("visibility"):
            node_data["visibility"] = item.get("visibility")
            
        if label not in self.nodes:
            self.nodes[label] = []
            
        if item.get("visibility") == "public":
            node_data["is_exported"] = True
            
        self.nodes[label].append(node_data)
        
        # Add CONTAINS relationship
        self.relationships["CONTAINS"].append({
            "from_label": parent_label, "from_id": parent_id,
            "to_label": label, "to_id": symbol_id
        })
        
        # Process children recursively
        for child in item.get("children", []):
            self._process_item(child, symbol_id, label)
            
        # Process docstrings if present
        doc_comment = item.get("doc_comment")
        if doc_comment:
            doc_id = f"{self.project_id}:doc:{self.rel_path}:{name}_doc"
            self.nodes["Documentation"].append({
                "id": doc_id, "name": f"{name} docs", "project_id": self.project_id,
                "filepath": self.rel_path, "start_line": start_line,
                "content": doc_comment
            })
            self.relationships["CONTAINS"].append({
                "from_label": label, "from_id": symbol_id,
                "to_label": "Documentation", "to_id": doc_id
            })

    def build(self) -> bool:
        """Analyze source code and populate nodes/relationships."""
        try:
            import tree_sitter_language_pack as ts_pack
            with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()

            _debug(f"Building graph for {self.file_path} ({self.lang_name})")

            # High-level process() API (returns a Python dict)
            try:
                if self.lang_name not in ts_pack.available_languages():
                    _debug(f"Downloading tree-sitter grammar for {self.lang_name}...")
                    ts_pack.download([self.lang_name])
                    
                config = ts_pack.ProcessConfig.all(self.lang_name)
                result = ts_pack.process(source, config)
                
                # result is a dictionary: {'structure': [...], 'symbols': [...], ...}
                structure = result.get("structure", [])
                if structure:
                    for item in structure:
                        self._process_item(item, self.file_id)
                        
                # Process imports (dependencies)
                imports = result.get("imports", [])
                for i, imp in enumerate(imports):
                    imp_source = imp.get("source", "unknown")
                    imp_id = f"{self.project_id}:import:{self.rel_path}:{i}"
                    
                    self.nodes["Import"].append({
                        "id": imp_id,
                        "source": imp_source,
                        "is_wildcard": imp.get("is_wildcard", False),
                        "project_id": self.project_id,
                        "filepath": self.rel_path
                    })
                    
                    self.relationships["HAS_IMPORT"].append({
                        "from_label": "File", "from_id": self.file_id,
                        "to_label": "Import", "to_id": imp_id
                    })
                
                # Check metrics for documentation linkage
                # metrics = result.get("metrics", {})

                return True 
            except Exception as e:
                _debug(f"ts_pack.process error for {self.lang_name}", error=str(e))
                return True

        except Exception as e:
            print(f"  Error in TreeSitterGraphBuilder for {self.file_path}: {e}", file=sys.stderr)
            return False

def _parse_swift_sourcekitten(source: str, builder: ASTGraphBuilder):
    """Fallback high-fidelity Swift structure indexing."""
    try:
        proc = subprocess.run(
            ["sourcekitten", "structure", "--text", source],
            capture_output=True,
            text=True,
            timeout=10
        )
        if proc.returncode != 0:
            return

        ast_data = json.loads(proc.stdout)
        substructure = ast_data.get("key.substructure", [])
        _process_swift_nodes(substructure, builder)
    except Exception:
        pass

def _process_swift_nodes(nodes: list, builder: ASTGraphBuilder, current_type_id: Optional[str] = None):
    for node in nodes:
        kind = node.get("key.kind", "")
        name = node.get("key.name", "unknown")
        
        is_type = kind in (
            "source.lang.swift.decl.class", "source.lang.swift.decl.struct",
            "source.lang.swift.decl.enum", "source.lang.swift.decl.protocol"
        )
        is_func = kind.startswith("source.lang.swift.decl.function")

        if is_type:
            type_id = builder._make_class_id(name)
            builder.nodes["Class"].append({"id": type_id, "name": name, "project_id": builder.project_id})
            builder.relationships["CONTAINS"].append({
                "from_label": "File", "from_id": builder.file_id,
                "to_label": "Class", "to_id": type_id
            })
            _process_swift_nodes(node.get("key.substructure", []), builder, type_id)
        elif is_func:
            func_id = f"{builder.project_id}:func:{builder.file_path}.{name}"
            builder.nodes["Function"].append({"id": func_id, "name": name, "project_id": builder.project_id})
            parent_id = current_type_id or builder.file_id
            parent_label = "Class" if current_type_id else "File"
            builder.relationships["CONTAINS"].append({
                "from_label": parent_label, "from_id": parent_id,
                "to_label": "Function", "to_id": func_id
            })

async def flush_graph_buffer(driver, project_id: str, builder: ASTGraphBuilder):
    """Batch insert nodes and relationships into Neo4j."""
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        # 1. Batch create nodes
        for label, batch in builder.nodes.items():
            if not batch: continue
            
            if label == "File":
                query = """
                UNWIND $batch AS p 
                MERGE (n:File {id: p.id}) 
                SET n += p 
                WITH n, p
                MATCH (proj:Project {id: p.project_id})
                MERGE (proj)-[:HAS_FILE]->(n)
                """
            elif label in ["Class", "Struct", "Interface", "Enum", "Trait", "Impl", "Module", "Namespace", "Symbol"]:
                key_map = {
                    "Class": "Class", "Struct": "Struct", "Interface": "Interface",
                    "Enum": "Enum", "Trait": "Trait", "Impl": "Impl",
                    "Module": "Module", "Namespace": "Namespace", "Symbol": "Symbol"
                }
                neo4j_label = key_map.get(label, "Symbol")
                query = f"UNWIND $batch AS p MERGE (n:{neo4j_label} {{id: p.id}}) SET n += p"
            elif label == "Function":
                query = "UNWIND $batch AS p MERGE (n:Function {id: p.id}) SET n += p"
            elif label == "Documentation":
                query = "UNWIND $batch AS p MERGE (n:Documentation {id: p.id}) SET n += p"
            else:
                query = f"UNWIND $batch AS p MERGE (n:{label} {{id: p.id}}) SET n += p"
                
            try:
                await session.run(query, batch=batch)
            except Exception as e:
                print(f"Failed to insert nodes for label {label}: {e}", file=sys.stderr)

        # 2. Batch create relationships
        rel_queries = {
            "CONTAINS": "UNWIND $batch AS rel MATCH (a), (b) WHERE a.id = rel.from_id AND b.id = rel.to_id MERGE (a)-[:CONTAINS]->(b)",
            "INHERITS": "UNWIND $batch AS rel MATCH (a), (b) WHERE a.id = rel.from_id AND b.id = rel.to_id MERGE (a)-[:INHERITS]->(b)",
            "CALLS": "UNWIND $batch AS rel MATCH (a), (b) WHERE a.id = rel.from_id AND b.id = rel.to_id MERGE (a)-[:CALLS]->(b)",
            "HAS_IMPORT": "UNWIND $batch AS rel MATCH (a), (b) WHERE a.id = rel.from_id AND b.id = rel.to_id MERGE (a)-[:HAS_IMPORT]->(b)"
        }
        
        for rel_type, rels in builder.relationships.items():
            if not rels: continue
            
            query = rel_queries.get(rel_type)
            if query:
                try:
                    await session.run(query, batch=rels)
                except Exception as e:
                    print(f"Failed to insert relationships for type {rel_type}: {e}", file=sys.stderr)
            else:
                # Fallback for unknown relationship types, using dynamic labels
                # Map labels to avoid schema issues
                for rel in rels:
                    from_l = rel["from_label"]
                    to_l = rel["to_label"]
                    batch = [rel]
                    
                    query = f"""
                    UNWIND $batch AS row
                    MATCH (a:{from_l} {{id: row.from_id}})
                    MATCH (b:{to_l} {{id: row.to_id}})
                    MERGE (a)-[:{rel_type}]->(b)
                    """
                    try:
                        await session.run(query, batch=batch)
                    except Exception as e:
                        pass

async def get_file_mtimes(driver, project_id: str) -> Dict[str, float]:
    mtimes = {}
    query = "MATCH (f:File {project_id: $pid}) RETURN f.rel_path AS path, f.mtime AS mtime"
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        result = await session.run(query, pid=project_id)
        async for record in result:
            mtimes[record["path"]] = record["mtime"]
    return mtimes

async def prune_file_nodes(driver, file_id: str):
    query = """
    MATCH (f:File {id: $fid})
    OPTIONAL MATCH (f)-[:CONTAINS*1..]->(child)
    WHERE child:Class OR child:Function OR child:Documentation OR child:Struct OR child:Trait OR child:Impl
    DETACH DELETE child
    """
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        await session.run(query, fid=file_id)

async def _link_documentation_to_symbols(driver, project_id: str):
    query = """
    MATCH (d:Documentation {project_id: $pid})
    MATCH (s) WHERE (s:Class OR s:Function OR s:Struct OR s:Trait) AND s.project_id = $pid
    AND (d.name CONTAINS s.name)
    MERGE (d)-[:MENTIONS]->(s)
    """
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        await session.run(query, pid=project_id)

TS_LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".rs": "rust", ".go": "go", ".cpp": "cpp", ".c": "c", ".java": "java",
    ".swift": "swift", ".rb": "ruby", ".php": "php", ".cs": "c_sharp",
    ".xml": "xml", ".html": "html"
}

async def index_project(target_dir: str, project_id: str):
    print(f"Bootstrapping Graph database...", file=sys.stderr)
    await graph_bootstrap.init_graph_db()
    driver = get_neo4j_driver()

    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        await session.run("MERGE (p:Project {id: $pid}) SET p.path = $path", pid=project_id, path=target_dir)

    existing_mtimes = await get_file_mtimes(driver, project_id)
    files_to_index = []
    for root, _, files in os.walk(target_dir):
        for file in files:
            if any(file.endswith(ext) for ext in TS_LANG_MAP.keys()):
                files_to_index.append(os.path.join(root, file))

    print(f"Checking {len(files_to_index)} files for changes in Project: {project_id}...", file=sys.stderr)
    for filepath in files_to_index:
        rel_path = os.path.relpath(filepath, target_dir)
        mtime = os.path.getmtime(filepath)
        
        if rel_path in existing_mtimes and abs(existing_mtimes[rel_path] - mtime) < 0.1:
            continue

        print(f"Indexing: {rel_path} (mtime changed)", file=sys.stderr)
        await prune_file_nodes(driver, f"{project_id}:{rel_path}")
        
        builder = ASTGraphBuilder(rel_path, project_id)
        builder.nodes["File"].append({
            "id": f"{project_id}:{rel_path}", "rel_path": rel_path,
            "project_id": project_id, "mtime": mtime
        })

        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == ".py":
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    source = f.read()
                import ast
                tree = ast.parse(source)
                builder.visit(tree)
            elif ext == ".swift":
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    _parse_swift_sourcekitten(f.read(), builder)
            
            lang_name = TS_LANG_MAP.get(ext)
            if lang_name:
                ts_builder = TreeSitterGraphBuilder(filepath, rel_path, project_id, lang_name)
                if ts_builder.build():
                    for label, nodes in ts_builder.nodes.items():
                        if label != "File":
                            if label not in builder.nodes:
                                builder.nodes[label] = []
                            builder.nodes[label].extend(nodes)
                    for rel_type, rels in ts_builder.relationships.items():
                        if rel_type not in builder.relationships:
                            builder.relationships[rel_type] = []
                        builder.relationships[rel_type].extend(rels)

            await flush_graph_buffer(driver, project_id, builder)
        except Exception as e:
            print(f"Failed to index {filepath}: {e}", file=sys.stderr)

    await _link_documentation_to_symbols(driver, project_id)
    print(f"Graph sync complete! (Project: {project_id}).", file=sys.stderr)
    await driver.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python graph_indexer.py <target_directory> [project_id]")
        sys.exit(1)
    target = os.path.abspath(sys.argv[1])
    pid = sys.argv[2] if len(sys.argv) > 2 else hashlib.md5(target.encode()).hexdigest()[:12]
    asyncio.run(index_project(target, pid))
