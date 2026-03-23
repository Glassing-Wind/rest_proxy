#!/usr/bin/env python3
"""
graph_indexer.py - Neo4j AST Structural Indexer

Parses a given Python workspace, extracts the AST (Classes, Functions, Calls, etc),
and indexes the semantic structural graph directly into the local Neo4j desktop instance.

Usage:
  python graph_indexer.py /path/to/project [project_id]
"""

import ast
import os
import sys
import hashlib
import asyncio
import subprocess
import json
from typing import Dict, List, Set, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load configuration so graph_bootstrap connects properly
from dotenv import load_dotenv
load_dotenv("/Users/michaelmarler/Projects/rest_proxy/.env")
import re


import graph_bootstrap

INDEXER_VERSION = "1.2.0-COLON-ID-FIX"

def _debug(msg: str, error: Optional[str] = None):
    prefix = f"[DEBUG] {msg}"
    if error:
        print(f"{prefix} | Error: {error}", file=sys.stderr)
    else:
        print(prefix)

class ASTGraphBuilder(ast.NodeVisitor):
    def __init__(self, file_path: str, project_id: str):
        self.file_path = file_path
        self.project_id = project_id
        self.nodes = {"File": [], "Class": [], "Function": [], "Documentation": []}
        self.relationships = {"CONTAINS": [], "INHERITS": [], "CALLS": []}
        self.current_class: Optional[str] = None
        self.current_function: Optional[str] = None
        self.file_id = f"{project_id}:{self.file_path}"
        self.nodes["File"].append({"id": self.file_id, "path": self.file_path, "project_id": project_id})

    def _make_class_id(self, name: str) -> str:
        return f"{self.project_id}:class:{name}"

    def _make_func_id(self, name: str, class_owner: Optional[str] = None) -> str:
        if class_owner:
            return f"{self.project_id}:func:{class_owner}.{name}"
        return f"{self.project_id}:func:{self.file_path}.{name}"

    def visit_ClassDef(self, node: ast.ClassDef):
        class_id = self._make_class_id(node.name)
        self.nodes["Class"].append({"id": class_id, "name": node.name, "project_id": self.project_id})
        self.relationships["CONTAINS"].append({"from_label": "File", "from_id": self.file_id, "to_label": "Class", "to_id": class_id})
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_class_id = self._make_class_id(base.id)
                self.relationships["INHERITS"].append({"from_label": "Class", "from_id": class_id, "to_label": "Class", "to_id": base_class_id})
        old_class = self.current_class
        self.current_class = class_id
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node)

    def _handle_function(self, node: ast.AST):
        func_id = self._make_func_id(node.name, self.current_class)
        container_label, container_id = ("Class", self.current_class) if self.current_class else ("File", self.file_id)
        self.nodes["Function"].append({"id": func_id, "name": node.name, "project_id": self.project_id})
        self.relationships["CONTAINS"].append({"from_label": container_label, "from_id": container_id, "to_label": "Function", "to_id": func_id})
        old_func = self.current_function
        self.current_function = func_id
        self.generic_visit(node)
        self.current_function = old_func

    def visit_Call(self, node: ast.Call):
        if self.current_function:
            callee_name = None
            if isinstance(node.func, ast.Name): callee_name = node.func.id
            elif isinstance(node.func, ast.Attribute): callee_name = node.func.attr
            if callee_name:
                callee_id = f"{self.project_id}:func:{callee_name}"
                self.relationships["CALLS"].append({"from_label": "Function", "from_id": self.current_function, "to_label": "Function", "to_id": callee_id, "fn_name": callee_name, "project_id": self.project_id})

    def build(self):
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
                tree = ast.parse(f.read())
            self.visit(tree)
            return True
        except Exception as e:
            _debug(f"ast_parse_error for {self.file_path}", error=str(e))
            return False

# --- Tree-Sitter Polyglot Integration ---

# Map file extensions to tree-sitter language names
TS_LANG_MAP = {
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".rs": "rust",
    ".java": "java", ".php": "php", ".sh": "bash", ".sql": "sql",
    ".md": "markdown", ".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml"
}

# Generic queries that work across many C-like languages for Classes and Functions
# We use capture names like 'class.name', 'class.base', 'function.name', and 'call.name'
TS_QUERIES = {
    "cpp": """
        (class_specifier 
            name: (type_identifier) @class.name
            (base_class_clause (type_identifier) @class.base)? 
        ) @class.wrapper
        (struct_specifier 
            name: (type_identifier) @class.name
            (base_class_clause (type_identifier) @class.base)?
        ) @class.wrapper
        (function_definition
          declarator: (function_declarator
            declarator: [
              (identifier) @function.name
              (field_identifier) @function.name
            ]
          )
        ) @function.wrapper
        (function_definition
          declarator: [
             (identifier) @function.name
             (field_identifier) @function.name
          ]
        ) @function.wrapper
        (call_expression function: (identifier) @call.name) @call.wrapper
    """,
    "c": """
        (struct_specifier name: (type_identifier) @class.name) @class.wrapper
        (function_definition
          declarator: (function_declarator
            declarator: [
              (identifier) @function.name
              (field_identifier) @function.name
            ]
          )
        ) @function.wrapper
        (call_expression function: (identifier) @call.name) @call.wrapper
    """,
    "javascript": """
        (class_declaration name: (identifier) @class.name) @class.wrapper
        (function_declaration name: (identifier) @function.name) @function.wrapper
        (method_definition name: (property_identifier) @function.name) @function.wrapper
        (call_expression function: (identifier) @call.name) @call.wrapper
    """,
    "typescript": """
        (class_declaration 
            name: (type_identifier) @class.name
            (class_heritage (type_identifier) @class.base)?
        ) @class.wrapper
        (interface_declaration name: (type_identifier) @class.name) @class.wrapper
        (function_declaration name: (identifier) @function.name) @function.wrapper
        (method_definition name: (property_identifier) @function.name) @function.wrapper
        (call_expression function: (identifier) @call.name) @call.wrapper
    """,
    "tsx": """
        (class_declaration 
            name: (type_identifier) @class.name
            (class_heritage (type_identifier) @class.base)?
        ) @class.wrapper
        (interface_declaration name: (type_identifier) @class.name) @class.wrapper
        (function_declaration name: (identifier) @function.name) @function.wrapper
        (method_definition name: (property_identifier) @function.name) @function.wrapper
        (call_expression function: (identifier) @call.name) @call.wrapper
    """,
    "markdown": """
        (atx_heading (inline) @doc.name) @doc.wrapper
    """,
    "json": """
        (pair
            key: (string) @doc.name
        ) @doc.wrapper
    """,
    "toml": """
        (pair
            (_) @doc.name
        ) @doc.wrapper
    """,
    "yaml": """
        (block_mapping_pair
            key: (_) @doc.name
        ) @doc.wrapper
    """
}

class TreeSitterGraphBuilder:
    def __init__(self, file_path: str, rel_path: str, project_id: str, lang_name: str):
        self.file_path = file_path # Absolute path for file reading
        self.rel_path = rel_path   # Relative path for ID stability
        self.project_id = project_id
        self.lang_name = lang_name
        
        self.nodes = {"File": [], "Class": [], "Function": [], "Documentation": []}
        self.relationships = {"CONTAINS": [], "INHERITS": [], "CALLS": []}
        
        self.file_id = f"{project_id}:{self.rel_path}"
        self.nodes["File"].append({"id": self.file_id, "path": self.rel_path, "project_id": project_id})

    def _process_item(self, item: dict, parent_id: str):
        """Recursively process structure items and build relationships."""
        kind = item.get("kind")
        name = item.get("name")
        span = item.get("span", {})
        children = item.get("children", [])
        
        if not name or not kind:
            return

        target_id = None
        if kind in ["Class", "Interface", "Struct", "Trait"]:
            target_id = f"{self.project_id}:class:{name}"
            self.nodes["Class"].append({
                "id": target_id, "name": name, "project_id": self.project_id,
                "file_id": self.file_id, "line": span.get("start_line", 0)
            })
            self.relationships["CONTAINS"].append({"from": parent_id, "to": target_id})
            
        elif kind in ["Function", "Method", "Constructor"]:
            target_id = f"{self.project_id}:function:{name}"
            self.nodes["Function"].append({
                "id": target_id, "name": name, "project_id": self.project_id,
                "file_id": self.file_id, "line": span.get("start_line", 0)
            })
            self.relationships["CONTAINS"].append({"from": parent_id, "to": target_id})
            
        elif kind in ["Heading", "Property", "Key"]:
            target_id = f"{self.project_id}:doc:{hashlib.md5(name.encode()).hexdigest()[:8]}"
            self.nodes["Documentation"].append({
                "id": target_id, "name": name, "project_id": self.project_id,
                "file_id": self.file_id
            })
            self.relationships["CONTAINS"].append({"from": parent_id, "to": target_id})

        # Recursively process children
        if target_id:
            for child in children:
                self._process_item(child, target_id)

    def build(self):
        try:
            import tree_sitter
            import tree_sitter_language_pack as ts_pack
            from tree_sitter import Query, QueryCursor
            
            # Ensure language is available
            manifest = ts_pack.manifest_languages()
            lang = None
            
            if self.lang_name in manifest:
                downloaded = ts_pack.downloaded_languages()
                if self.lang_name not in downloaded:
                    _debug(f"Downloading tree-sitter grammar for {self.lang_name}...")
                    ts_pack.download([self.lang_name])
                lang = ts_pack.get_language(self.lang_name)
            else:
                # Try standalone package as fallback (e.g. tree-sitter-yaml)
                try:
                    import importlib
                    standalone_pkg = importlib.import_module(f"tree_sitter_{self.lang_name}")
                    lang = tree_sitter.Language(standalone_pkg.language())
                    _debug(f"Using standalone grammar package for {self.lang_name}")
                except (ImportError, AttributeError) as e:
                    _debug(f"Failed to load standalone grammar for {self.lang_name}", error=str(e))

            if not lang:
                return True # Unsupported but not an error
                
            with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
            
            # Try high-level process() if supported by the pack
            if self.lang_name in manifest:
                try:
                    config = ts_pack.ProcessConfig(language=self.lang_name)
                    result = ts_pack.process(source, config)
                    structure = result.get("structure", [])
                    if structure:
                        _debug(f"Source {self.file_path} indexed via process() ({len(structure)} top-level items)")
                        for item in structure:
                            self._process_item(item, self.file_id)
                        return True
                except Exception as e:
                    _debug(f"ts_pack.process error for {self.lang_name}", error=str(e))

            # --- Fallback to Manual Queries for Docs/Data formats ---
            parser = tree_sitter.Parser(lang)
            source_bytes = source.encode("utf-8")
            tree = parser.parse(source_bytes)
            
            if self.lang_name not in TS_QUERIES:
                return True
                
            query = Query(lang, TS_QUERIES[self.lang_name])
            cursor = QueryCursor(query)
            matches = list(cursor.matches(tree.root_node))
            
            if matches:
                _debug(f"Source {self.file_path} indexed via manual query ({len(matches)} matches)")
            
            captures_dict = {}
            for _, match_captures in matches:
                for name, nodes in match_captures.items():
                    if name not in captures_dict:
                        captures_dict[name] = []
                    captures_dict[name].extend(nodes)

            # [Manual capture loop continues below as before...]
            
            # Map of Tree-Sitter node IDs to our generated Neo4j IDs
            # This allows us to link calls back to their containing functions
            node_to_id = {}
            
            # 1. First Pass: Identify Symbols (Nodes)
            for capture_name, nodes in captures_dict.items():
                for node in nodes:
                    name = node.text.decode(errors='ignore').strip()
                    start_line = node.start_point[0] + 1
                    
                    if capture_name == "doc.name":
                        doc_id = f"{self.project_id}:doc:{self.rel_path}:{name}"
                        self.nodes["Documentation"].append({
                            "id": doc_id, "name": name, "project_id": self.project_id,
                            "filepath": self.rel_path, "start_line": start_line
                        })
                        self.relationships["CONTAINS"].append({
                            "from_label": "File", "from_id": self.file_id,
                            "to_label": "Documentation", "to_id": doc_id
                        })
                        
                    elif capture_name == "class.name":
                        class_id = f"{self.project_id}:class:{name}"
                        # Link to parent (File or Class)
                        parent = node.parent
                        container_id = self.file_id
                        container_label = "File"
                        while parent:
                            if parent.id in node_to_id and node_to_id[parent.id].split(":")[1] == "class":
                                container_id = node_to_id[parent.id]
                                container_label = "Class"
                                break
                            parent = parent.parent
                            
                        self.nodes["Class"].append({
                            "id": class_id, "name": name, "project_id": self.project_id,
                            "filepath": self.file_path, "start_line": start_line
                        })
                        self.relationships["CONTAINS"].append({
                            "from_label": container_label, "from_id": container_id,
                            "to_label": "Class", "to_id": class_id
                        })
                        node_to_id[node.parent.id if node.parent else node.id] = class_id
                        
                    elif capture_name == "function.name":
                        func_name = name
                        # Find container
                        parent = node.parent
                        container_id = self.file_id
                        container_label = "File"
                        while parent:
                            if parent.id in node_to_id:
                                current_id = node_to_id[parent.id]
                                if ":class:" in current_id:
                                    container_id = current_id
                                    container_label = "Class"
                                    # Form scoped name
                                    class_name = container_id.split(":")[-1]
                                    func_name = f"{class_name}.{name}"
                                    break
                            parent = parent.parent
                            
                        func_id = f"{self.project_id}:func:{self.file_path}.{func_name}"
                        self.nodes["Function"].append({
                            "id": func_id, "name": name, "project_id": self.project_id,
                            "filepath": self.file_path, "start_line": start_line
                        })
                        self.relationships["CONTAINS"].append({
                            "from_label": container_label, "from_id": container_id,
                            "to_label": "Function", "to_id": func_id
                        })
                        # Store the wrapper node ID so calls can find it
                        wrapper = node
                        while wrapper and wrapper.type not in ("function_definition", "method_definition", "function_declaration"):
                            wrapper = wrapper.parent
                        if wrapper:
                            node_to_id[wrapper.id] = func_id

            # 2. Second Pass: Identify Relationships (Inheritance and Calls)
            for capture_name, nodes in captures_dict.items():
                for node in nodes:
                    name = node.text.decode(errors='ignore')
                    
                    if capture_name == "class.base":
                        # Find which class this base belongs to
                        parent = node.parent
                        while parent and parent.id not in node_to_id:
                            parent = parent.parent
                        if parent:
                            class_id = node_to_id[parent.id]
                            base_id = f"{self.project_id}:class:{name}"
                            self.relationships["INHERITS"].append({
                                "from_label": "Class", "from_id": class_id,
                                "to_label": "Class", "to_id": base_id
                            })
                            
                    elif capture_name == "call.name":
                        # Find which function this call is inside of
                        parent = node.parent
                        while parent and parent.id not in node_to_id:
                            parent = parent.parent
                        
                        if parent:
                            caller_id = node_to_id[parent.id]
                            # Callee is a best-effort global link for now
                            callee_id = f"{self.project_id}:global:{name}"
                            self.relationships["CALLS"].append({
                                "from_label": "Function", "from_id": caller_id,
                                "to_label": "Function", "to_id": callee_id,
                                "fn_name": name, "project_id": self.project_id
                            })

            return True
        except Exception as e:
            print(f"  Error in TreeSitterGraphBuilder for {self.file_path}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return False

def _parse_swift_sourcekitten(source: str, builder: ASTGraphBuilder):
    """Use SourceKitten for high-fidelity Swift structure indexing."""
    try:
        proc = subprocess.run(
            ["sourcekitten", "structure", "--text", source],
            capture_output=True,
            text=True,
            timeout=10
        )
        if proc.returncode != 0:
            print(f"SourceKitten failed for {builder.file_path}: {proc.stderr[:200]}", file=sys.stderr)
            return

        ast_data = json.loads(proc.stdout)
        substructure = ast_data.get("key.substructure", [])
        _process_swift_nodes(substructure, builder)
    except Exception as e:
        print(f"Error parsing Swift with SourceKitten for {builder.file_path}: {e}", file=sys.stderr)

def _process_swift_nodes(nodes: list, builder: ASTGraphBuilder, current_type_id: Optional[str] = None):
    """Recursively process SourceKitten substructure into Neo4j nodes/rels."""
    for node in nodes:
        kind = node.get("key.kind", "")
        name = node.get("key.name", "unknown")
        
        # 1. Handle Types (Class, Struct, Enum, Protocol, extension)
        is_type = kind in (
            "source.lang.swift.decl.class", 
            "source.lang.swift.decl.struct",
            "source.lang.swift.decl.enum",
            "source.lang.swift.decl.protocol",
            "source.lang.swift.decl.extension"
        )
        
        # 2. Handle Functions / Methods
        is_func = kind.startswith("source.lang.swift.decl.function")

        if is_type:
            type_id = builder._make_class_id(name)
            builder.nodes["Class"].append({
                "id": type_id,
                "name": name,
                "project_id": builder.project_id
            })
            
            # Map inheritance (inherited types)
            inherited_types = node.get("key.inheritedtypes", [])
            for base in inherited_types:
                base_name = base.get("key.name")
                if base_name:
                    base_id = builder._make_class_id(base_name)
                    builder.relationships["INHERITS"].append({
                        "from_label": "Class", "from_id": type_id,
                        "to_label": "Class", "to_id": base_id
                    })

            # Establish CONTAINS relationship
            if current_type_id:
                # Nested type
                builder.relationships["CONTAINS"].append({
                    "from_label": "Class", "from_id": current_type_id,
                    "to_label": "Class", "to_id": type_id
                })
            else:
                # Top-level type in file
                builder.relationships["CONTAINS"].append({
                    "from_label": "File", "from_id": builder.file_id,
                    "to_label": "Class", "to_id": type_id
                })
            
            # Recurse into subtypes/methods
            substructure = node.get("key.substructure", [])
            if substructure:
                _process_swift_nodes(substructure, builder, type_id)

        elif is_func:
            func_id = builder._make_func_id(name, current_type_id)
            builder.nodes["Function"].append({
                "id": func_id,
                "name": name,
                "project_id": builder.project_id
            })
            
            # Establish CONTAINS relationship
            if current_type_id:
                container_label, container_id = "Class", current_type_id
            else:
                container_label, container_id = "File", builder.file_id
                
            builder.relationships["CONTAINS"].append({
                "from_label": container_label, "from_id": container_id,
                "to_label": "Function", "to_id": func_id
            })
            # (Optional) Recurse if we wanted local functions - but usually we don't for high-level graph

async def flush_graph_buffer(driver, buffer: ASTGraphBuilder):
    """Writes the extracted graph directly into the Neo4j Database."""
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        # 1. Write Nodes
        for label, nodes in buffer.nodes.items():
            if not nodes:
                continue
            
            # Using UNWIND for batch inserts based on label
            if label == "File":
                query = """
                UNWIND $batch AS p 
                MERGE (n:File {id: p.id}) 
                SET n += p 
                WITH n, p
                MATCH (proj:Project {id: p.project_id})
                MERGE (proj)-[:HAS_FILE]->(n)
                """
            elif label == "Class":
                query = "UNWIND $batch AS p MERGE (n:Class {id: p.id}) SET n += p"
            elif label == "Function":
                query = "UNWIND $batch AS p MERGE (n:Function {id: p.id}) SET n.name = p.name, n.project_id = p.project_id"
            elif label == "Documentation":
                query = "UNWIND $batch AS p MERGE (n:Documentation {id: p.id}) SET n += p"
                
            if query:
                await session.run(query, batch=nodes)
            else:
                print(f"Warning: No query for label {label}", file=sys.stderr)
                
        # 2. Write Relationships
        for rel_type, rels in buffer.relationships.items():
            if not rels:
                continue
            
            # Neo4j Cypher does not allow dynamic labels in MERGE statements easily, 
            # so we group relationships by (FromLabel, ToLabel).
            grouped_rels = {}
            for r in rels:
                key = (r["from_label"], r["to_label"])
                grouped_rels.setdefault(key, []).append(r)
                
            for (from_l, to_l), batch in grouped_rels.items():
                if rel_type == "CALLS":
                    # For CALLS, the destination function might not have been created yet (or might be external).
                    # We MERGE the destination node blindly first.
                    query = f"""
                    UNWIND $batch AS row
                    MATCH (a:{from_l} {{id: row.from_id}})
                    MERGE (b:{to_l} {{id: row.to_id}})
                    ON CREATE SET b.name = row.fn_name, b.project_id = row.project_id
                    MERGE (a)-[:CALLS]->(b)
                    """
                else:
                    query = f"""
                    UNWIND $batch AS row
                    MATCH (a:{from_l} {{id: row.from_id}})
                    MATCH (b:{to_l} {{id: row.to_id}})
                    MERGE (a)-[:{rel_type}]->(b)
                    """
                try:
                    await session.run(query, batch=batch)
                except Exception as e:
                    print(f"Failed to insert relation {rel_type} between {from_l} and {to_l}: {e}", file=sys.stderr)

async def get_file_mtimes(driver, project_id: str) -> Dict[str, float]:
    """Fetch existing file mtimes from Neo4j to determine what needs re-indexing."""
    mtimes = {}
    query = "MATCH (f:File {project_id: $pid}) RETURN f.rel_path AS path, f.mtime AS mtime"
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        result = await session.run(query, pid=project_id)
        async for record in result:
            mtimes[record["path"]] = record["mtime"]
    return mtimes

async def prune_file_nodes(driver, file_id: str):
    """Remove all structural nodes (Classes, Functions) associated with a file before re-indexing."""
    # Recursively find all nodes contained by this file (Classes, and Functions inside them)
    query = """
    MATCH (f:File {id: $fid})
    OPTIONAL MATCH (f)-[:CONTAINS*1..]->(child)
    WHERE child:Class OR child:Function OR child:Documentation
    DETACH DELETE child
    """
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        await session.run(query, fid=file_id)

async def _link_documentation_to_symbols(driver, project_id: str):
    """Bridge documentation to code symbols by matching names."""
    # We look for documentation nodes that contain the name of a class or function
    # This creates a 'Hybrid Bridge' between human intent and implementation
    query = """
    MATCH (d:Documentation {project_id: $pid})
    MATCH (s) WHERE (s:Class OR s:Function) AND s.project_id = $pid
    AND (d.name CONTAINS s.name)
    MERGE (d)-[:MENTIONS]->(s)
    """
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        print(f"Linking documentation to symbols in project {project_id}...", file=sys.stderr)
        await session.run(query, pid=project_id)

async def cleanup_orphans(driver, project_id: str, active_file_ids: set):
    """Remove File nodes (and their children) that are no longer present on disk."""
    query = "MATCH (f:File {project_id: $pid}) RETURN f.id AS id"
    to_delete = []
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        result = await session.run(query, pid=project_id)
        async for record in result:
            fid = record["id"]
            if fid not in active_file_ids:
                to_delete.append(fid)
        
        if to_delete:
            print(f"Cleaning up {len(to_delete)} orphaned files from Neo4j...", file=sys.stderr)
            # Recursively delete the file and all its contents
            delete_query = """
            MATCH (f:File) 
            WHERE f.id IN $ids 
            OPTIONAL MATCH (f)-[:CONTAINS*1..]->(child)
            WHERE child:Class OR child:Function OR child:Documentation
            DETACH DELETE f, child
            """
            await session.run(delete_query, ids=to_delete)

async def index_project(target_dir: str, project_id: str):
    await graph_bootstrap.init_graph_db()
    driver = graph_bootstrap.get_driver()
    if not driver:
        print("Failed to connect to Neo4j. Aborting AST Graph Index.", file=sys.stderr)
        return
    
    # 0. Ensure Project node exists
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        await session.run("MERGE (p:Project {id: $pid}) SET p.path = $path", pid=project_id, path=target_dir)

    # 1. Get existing index state
    existing_mtimes = await get_file_mtimes(driver, project_id)
    active_file_ids = set()
    
    files_to_index = []
    for root, dirs, files in os.walk(target_dir):
        # Skip common non-source and data directories
        dirs[:] = [d for d in dirs if d not in {".git", "build", "Pods", ".build", "models", "weights", "checkpoints", "venv", "node_modules", "DerivedData"}]
        
        for name in files:
            if name.endswith((".py", ".swift", ".js", ".ts", ".jsx", ".tsx", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".c", ".cpp", ".h", ".hpp", ".sh", ".sql")):
                files_to_index.append(os.path.join(root, name))

    print(f"Checking {len(files_to_index)} files for changes in Project: {project_id}...", file=sys.stderr)
    
    total_files = 0
    updated_files = 0
    for filepath in files_to_index:
        rel_path = os.path.relpath(filepath, target_dir)
        file_id = f"{project_id}:{rel_path}"
        active_file_ids.add(file_id)
        
        mtime = os.path.getmtime(filepath)
        
        # Skip if unchanged
        if rel_path in existing_mtimes and abs(existing_mtimes[rel_path] - mtime) < 0.1:
            total_files += 1
            continue
            
        # Re-indexing needed
        print(f"Indexing: {rel_path} (mtime changed)", file=sys.stderr)
        builder = ASTGraphBuilder(rel_path, project_id)
        builder.nodes["File"].append({
            "id": file_id,
            "rel_path": rel_path,
            "project_id": project_id,
            "mtime": mtime
        })
        
        try:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".py":
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=filepath)
                builder.visit(tree)
            elif ext == ".swift":
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                _parse_swift_sourcekitten(source, builder)
            elif ext in TS_LANG_MAP:
                # Use Tree-Sitter for other languages
                lang_name = TS_LANG_MAP[ext]
                ts_builder = TreeSitterGraphBuilder(filepath, rel_path, project_id, lang_name)
                if ts_builder.build():
                    # Merge nodes and relationships from TS builder into the main builder buffer
                    for label, nodes in ts_builder.nodes.items():
                        if label != "File": # File node is already handled by main builder
                            builder.nodes[label].extend(nodes)
                    for rel_type, rels in ts_builder.relationships.items():
                        builder.relationships[rel_type].extend(rels)
        except Exception as e:
            print(f"Failed to index {filepath}: {e}", file=sys.stderr)
            continue
            
        # Prune old nodes for this file before flushing new ones
        await prune_file_nodes(driver, file_id)
        
        # Flush to DB
        await flush_graph_buffer(driver, builder)
        total_files += 1
        updated_files += 1

    # 2. Cleanup orphaned files (deleted from disk)
    await cleanup_orphans(driver, project_id, active_file_ids)
    
    # 3. Bridge Documentation to Code
    await _link_documentation_to_symbols(driver, project_id)

    print(f"Graph sync complete! Total={total_files}, Updated={updated_files} (Project: {project_id}).", file=sys.stderr)
    await graph_bootstrap.close_graph_db()

if __name__ == "__main__":
    _debug("Indexer script started")
    if len(sys.argv) < 2:
        print("Usage: python graph_indexer.py <target_directory> [project_id]", file=sys.stderr)
        sys.exit(1)
        
    target = os.path.abspath(sys.argv[1])
    pid = sys.argv[2] if len(sys.argv) > 2 else hashlib.md5(target.encode()).hexdigest()[:12]
    
    asyncio.run(index_project(target, pid))
