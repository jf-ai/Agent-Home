"""
Architecture diagram generator for Agent Home README.
Run with: uv run --with retroflow python3 docs/diagrams/architecture.py
Output is written to docs/diagrams/architecture.txt
"""

from retroflow import FlowchartGenerator

generator = FlowchartGenerator(shadow=False)

result = generator.generate("""
[Core Server: Agent Loop Memory System Message History]
[Filesystem Tools: Sandboxed Host-side]

Nori TUI -> Agent Loop
Management Console -> Agent Loop
Misc Interfaces -> Agent Loop
Agent Loop -> Memory System
Agent Loop -> Message History
Agent Loop -> Sandboxed
Agent Loop -> Host-side
""")

output_path = "docs/diagrams/architecture.txt"
with open(output_path, "w") as f:
    f.write(result)

print(f"Written to {output_path}")
print(f"({len(result.splitlines())} lines)")
