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
from typing import Dict, List, Set, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load configuration so graph_bootstrap connects properly
from dotenv import load_dotenv
load_dotenv("/Users/michaelmarler/Projects/rest_proxy/.env")
import re


import graph_bootstrap

class ASTGraphBuilder(ast.NodeVisitor):
    def __init__(self, file_path: str, project_id: str):
        self.file_path = file_path
        self.project_id = project_id
        
        # Cypher Write Buffers
        self.nodes = {
            "File": [],
            "Class": [],
            "Function": []
        }
        self.relationships = {
            "CONTAINS": [],   # File -> (Class | Function), Class -> Function
            "INHERITS": [],   # Class -> Class
            "CALLS": []       # Function -> Function
        }
        
        # Track current context for relationships
        self.current_class: Optional[str] = None
        self.current_function: Optional[str] = None
        
        # Global uniqueness requires deterministic IDs across the project
        self.file_id = f"{project_id}_{self.file_path}"
        self.nodes["File"].append({"id": self.file_id, "path": self.file_path, "project_id": project_id})

    def _make_class_id(self, name: str) -> str:
        return f"{self.project_id}_{name}"

    def _make_func_id(self, name: str, class_owner: Optional[str] = None) -> str:
        if class_owner:
            return f"{self.project_id}_{class_owner}.{name}"
        return f"{self.project_id}_{self.file_path}.{name}"

    def visit_ClassDef(self, node: ast.ClassDef):
        class_id = self._make_class_id(node.name)
        self.nodes["Class"].append({
            "id": class_id,
            "name": node.name,
            "project_id": self.project_id
        })
        self.relationships["CONTAINS"].append({
            "from_label": "File", "from_id": self.file_id,
            "to_label": "Class", "to_id": class_id
        })

        for base in node.bases:
            if isinstance(base, ast.Name):
                base_class_id = self._make_class_id(base.id)
                self.relationships["INHERITS"].append({
                    "from_label": "Class", "from_id": class_id,
                    "to_label": "Class", "to_id": base_class_id
                })

        old_class = self.current_class
        self.current_class = class_id
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_function(node)

    def _handle_function(self, node: ast.AST):
        func_id = self._make_func_id(node.name, getattr(self, "current_class", None))
        
        # Determine container
        if self.current_class:
            container_label, container_id = "Class", self.current_class
        else:
            container_label, container_id = "File", self.file_id

        self.nodes["Function"].append({
            "id": func_id,
            "name": node.name,
            "project_id": self.project_id
        })
        
        self.relationships["CONTAINS"].append({
            "from_label": container_label, "from_id": container_id,
            "to_label": "Function", "to_id": func_id
        })

        old_func = self.current_function
        self.current_function = func_id
        self.generic_visit(node)
        self.current_function = old_func

    def visit_Call(self, node: ast.Call):
        if self.current_function:
            callee_name = None
            if isinstance(node.func, ast.Name):
                callee_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee_name = node.func.attr
            
            if callee_name:
                # We do a best-effort guess at the callee ID. 
                # If it's an imported module function, it might just be the name.
                # If it's a class method, the id might be project_Class.method. 
                # Since AST doesn't have a type checker, we link to a fuzzy function node.
                # A more advanced indexer (like SourceKitten) would know the exact symbol ID.
                # For this MVP Python builder, we'll try to link to the global namespace.
                # If we don't know the owner class, we use a generic placeholder ID.
                callee_tmp_id = f"{self.project_id}_global_{callee_name}"
                
                self.relationships["CALLS"].append({
                    "from_label": "Function", "from_id": self.current_function,
                    "to_label": "Function", "to_id": callee_tmp_id,
                    "fn_name": callee_name
                })
        self.generic_visit(node)

def _parse_swift_regex(source: str, builder: ASTGraphBuilder):
    """Fallback regex parser for Swift structure."""
    # Match classes, structs, enums
    type_pattern = re.compile(r"(class|struct|enum|protocol|extension)\s+([A-Z][a-zA-Z0-9_]+)")
    # Match functions
    func_pattern = re.compile(r"func\s+([a-zA-Z0-9_]+)\s*\(")
    
    current_type = None
    
    # Very simple line-based extraction for MVP
    for line in source.split("\n"):
        t_match = type_pattern.search(line)
        if t_match:
            t_name = t_match.group(2)
            t_id = builder._make_class_id(t_name)
            builder.nodes["Class"].append({
                "id": t_id,
                "name": t_name,
                "project_id": builder.project_id
            })
            builder.relationships["CONTAINS"].append({
                "from_label": "File", "from_id": builder.file_id,
                "to_label": "Class", "to_id": t_id
            })
            current_type = t_id
            continue
            
        f_match = func_pattern.search(line)
        if f_match:
            f_name = f_match.group(1)
            f_id = builder._make_func_id(f_name, current_type)
            
            if current_type:
                container_label, container_id = "Class", current_type
            else:
                container_label, container_id = "File", builder.file_id
                
            builder.nodes["Function"].append({
                "id": f_id,
                "name": f_name,
                "project_id": builder.project_id
            })
            builder.relationships["CONTAINS"].append({
                "from_label": container_label, "from_id": container_id,
                "to_label": "Function", "to_id": f_id
            })

async def flush_graph_buffer(driver, buffer: ASTGraphBuilder):
    """Writes the extracted graph directly into the Neo4j Database."""
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        # 1. Write Nodes
        for label, nodes in buffer.nodes.items():
            if not nodes:
                continue
            
            # Using UNWIND for batch inserts based on label
            if label == "File":
                query = "UNWIND $batch AS p MERGE (n:File {id: p.id}) SET n += p"
            elif label == "Class":
                query = "UNWIND $batch AS p MERGE (n:Class {id: p.id}) SET n += p"
            elif label == "Function":
                query = "UNWIND $batch AS p MERGE (n:Function {id: p.id}) SET n.name = p.name, n.project_id = p.project_id"
                
            await session.run(query, batch=nodes)
                
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
                    ON CREATE SET b.name = row.fn_name
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
                    print(f"Failed to insert relation {rel_type} between {from_l} and {to_l}: {e}")

async def index_project(target_dir: str, project_id: str):
    await graph_bootstrap.init_graph_db()
    driver = graph_bootstrap.get_driver()
    if not driver:
        print("Failed to connect to Neo4j. Aborting AST Graph Index.")
        return
    files_to_index = []
    for root, _, files in os.walk(target_dir):
        if ".git" in root or "build" in root or "venv" in root or "node_modules" in root:
            continue
        for name in files:
            if name.endswith((".py", ".swift")):
                files_to_index.append(os.path.join(root, name))

    print(f"Found {len(files_to_index)} files to index into Neo4j graph.")
    
    total_files = 0
    for filepath in files_to_index:
        rel_path = os.path.relpath(filepath, target_dir)
        builder = ASTGraphBuilder(rel_path, project_id)
        
        try:
            if filepath.endswith(".py"):
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=filepath)
                builder.visit(tree)
            elif filepath.endswith(".swift"):
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                _parse_swift_regex(source, builder)
        except Exception as e:
            print(f"Failed to index {filepath}: {e}")
            continue
            
        # Flush to DB
        await flush_graph_buffer(driver, builder)
        total_files += 1

    print(f"Graph ingestion complete! Indexed AST for {total_files} files into Neo4j (Project: {project_id}).")
    await graph_bootstrap.close_graph_db()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python graph_indexer.py <target_directory> [project_id]")
        sys.exit(1)
        
    target = os.path.abspath(sys.argv[1])
    pid = sys.argv[2] if len(sys.argv) > 2 else hashlib.md5(target.encode()).hexdigest()[:12]
    
    asyncio.run(index_project(target, pid))
