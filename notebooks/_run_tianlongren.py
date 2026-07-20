"""Execute tianlongren_hit_rate_research.ipynb cells sequentially.

This is a one-shot driver. The .ipynb is the deliverable; this script
just runs its code cells in a shared namespace so we can produce the report
without a full Jupyter kernel.
"""
import json, sys, traceback
from pathlib import Path

NB = Path(__file__).parent / "tianlongren_hit_rate_research.ipynb"
nb = json.loads(NB.read_text(encoding="utf-8"))

ns: dict = {"__name__": "__main__"}
for i, cell in enumerate(nb["cells"]):
    if cell.get("cell_type") != "code":
        continue
    src = "".join(cell["source"])
    print(f"\n{'='*70}\n[CELL {i}]  (lines: {src.count(chr(10))+1})\n{'='*70}", flush=True)
    try:
        exec(compile(src, f"<cell {i}>", "exec"), ns)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
print("\nAll cells executed successfully.")
