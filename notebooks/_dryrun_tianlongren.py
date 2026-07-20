"""Dry-run: cells 0..4 only (no heavy options fetch). Validates setup + event detection."""
import json, sys, traceback
from pathlib import Path

NB = Path(__file__).parent / "tianlongren_hit_rate_research.ipynb"
nb = json.loads(NB.read_text(encoding="utf-8"))

ns: dict = {"__name__": "__main__"}
code_idx = 0
for i, cell in enumerate(nb["cells"]):
    if cell.get("cell_type") != "code":
        continue
    if code_idx >= 4:
        break
    src = "".join(cell["source"])
    print(f"\n=== CELL idx={i} (code_idx={code_idx}) ===", flush=True)
    try:
        exec(compile(src, f"<cell {i}>", "exec"), ns)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    code_idx += 1
print("\nDry-run OK.")
