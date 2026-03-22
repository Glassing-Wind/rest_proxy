import subprocess
import json
import logging
import ast

logger = logging.getLogger("lm_proxy.skeleton")

def extract_skeleton(code: str, file_path: str) -> str:
    """Extract structural skeleton based on file extension."""
    if file_path.endswith(".swift"):
        return _parse_swift(code)
    elif file_path.endswith(".py"):
        return _parse_python(code)
    elif file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
        return _parse_regex_fallback(code)
    return ""

def _parse_swift(code: str) -> str:
    """Use SourceKitten for perfect Swift AST parsing."""
    try:
        proc = subprocess.run(
            ["sourcekitten", "structure", "--text", code],
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode != 0:
            logger.warning(f"SourceKitten failed: {proc.stderr[:200]}")
            return _parse_regex_fallback(code)  # fall back if SK fails
            
        ast_data = json.loads(proc.stdout)
        substructure = ast_data.get("key.substructure", [])
        return _format_swift_ast(substructure, code)
    except Exception as e:
        logger.warning(f"Error parsing Swift: {e}")
        return _parse_regex_fallback(code)

def _format_swift_ast(nodes: list, code: str, indent: int = 0) -> str:
    lines = []
    prefix = "    " * indent
    
    for node in nodes:
        kind = node.get("key.kind", "")
        
        # We care about classes, structs, enums, protocols, funcs, and vars.
        # Ignore parameters inside functions
        is_var = kind == "source.lang.swift.decl.var.instance" or kind == "source.lang.swift.decl.var.static"
        is_type = kind in (
            "source.lang.swift.decl.class", 
            "source.lang.swift.decl.struct",
            "source.lang.swift.decl.enum",
            "source.lang.swift.decl.protocol",
            "source.lang.swift.decl.extension"
        )
        is_func = kind.startswith("source.lang.swift.decl.function")
        
        if is_type or is_func or is_var:
            # Extract the raw declaration text using byte offsets if available
            offset = node.get("key.offset")
            length = node.get("key.length")
            body_offset = node.get("key.bodyoffset")
            
            if offset is not None and body_offset is not None:
                # Capture just the signature (everything before the { body)
                sig_len = body_offset - offset
                sig = _extract_bytes(code, offset, sig_len).strip()
                if sig.endswith("{"):
                    sig = sig[:-1].strip()
                
                # Only recurse if it's a type (class/struct/etc). We don't want local vars inside funcs.
                children = node.get("key.substructure", []) if is_type else []
                
                if children:
                    lines.append(f"{prefix}{sig} {{")
                    lines.append(_format_swift_ast(children, code, indent + 1))
                    lines.append(f"{prefix}}}")
                else:
                    lines.append(f"{prefix}{sig} {{ ... }}")
            elif offset is not None and length is not None:
                # E.g. properties without bodies
                sig = _extract_bytes(code, offset, length).strip()
                lines.append(f"{prefix}{sig}")
                
    return "\n".join(lines)

def _extract_bytes(s: str, offset: int, length: int) -> str:
    """SourceKitten uses utf-8 byte offsets, not python string indices."""
    return s.encode('utf-8')[offset:offset+length].decode('utf-8', errors='ignore')

def _parse_python(code: str) -> str:
    """Use python's built-in AST module."""
    try:
        tree = ast.parse(code)
        lines = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                lines.append(f"class {node.name}:")
                for subnode in node.body:
                    if isinstance(subnode, ast.FunctionDef):
                        args = [a.arg for a in subnode.args.args]
                        lines.append(f"    def {subnode.name}({', '.join(args)}): ...")
                    elif isinstance(subnode, ast.AnnAssign) and isinstance(subnode.target, ast.Name):
                        # Class attributes
                        lines.append(f"    {subnode.target.id}: ...")
            elif isinstance(node, ast.FunctionDef):
                args = [a.arg for a in node.args.args]
                lines.append(f"def {node.name}({', '.join(args)}): ...")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Error parsing Python AST: {e}")
        return _parse_regex_fallback(code)

def _parse_regex_fallback(code: str) -> str:
    """A fast heuristic fallback for JS/TS/others."""
    import re
    lines = []
    # Match basic structural declarations: class, func, const, let
    pattern = re.compile(
        r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?'
        r'(?:class\s+\w+|function\s+\w+\s*\(.*?\)|const\s+\w+\s*=|let\s+\w+\s*=)',
        re.MULTILINE
    )
    for match in pattern.finditer(code):
        decl = match.group(0).strip()
        if decl.endswith('='):
            decl += ' ...'
        lines.append(decl)
    return "\n".join(lines)
