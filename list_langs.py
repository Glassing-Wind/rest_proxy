import tree_sitter_language_pack as ts_pack
import json

md_code = """
# Main Title
This is some text.

## Section 1
More text.

### Subsection 1.1
Even more text.
"""

config = ts_pack.ProcessConfig(language='markdown')
try:
    result = ts_pack.process(md_code, config)
    print("--- Markdown Process Inspection ---")
    structure = result.get("structure", [])
    print(json.dumps(structure, indent=2))
except Exception as e:
    print(f"Error processing markdown: {e}")
