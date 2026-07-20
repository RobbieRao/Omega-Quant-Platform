import { useMemo } from "react";

type Cell = { row: string | number; col: string | number; value: number | null };

export function Heatmap({
  data,
  rowLabel = "row",
  colLabel = "col",
  valueLabel = "value",
  formatValue = (v) => v.toFixed(3),
  height = 360,
}: {
  data: Cell[];
  rowLabel?: string;
  colLabel?: string;
  valueLabel?: string;
  formatValue?: (v: number) => string;
  height?: number;
}) {
  const { rows, cols, matrix, min, max } = useMemo(() => {
    const rowSet = new Set<string | number>();
    const colSet = new Set<string | number>();
    let mn = Infinity, mx = -Infinity;
    for (const c of data) {
      rowSet.add(c.row);
      colSet.add(c.col);
      if (c.value !== null && Number.isFinite(c.value)) {
        mn = Math.min(mn, c.value);
        mx = Math.max(mx, c.value);
      }
    }
    const rows = Array.from(rowSet).sort((a, b) => Number(a) - Number(b));
    const cols = Array.from(colSet).sort((a, b) => Number(a) - Number(b));
    const matrix: (number | null)[][] = rows.map(() => cols.map(() => null));
    for (const c of data) {
      const ri = rows.indexOf(c.row);
      const ci = cols.indexOf(c.col);
      if (ri >= 0 && ci >= 0) matrix[ri][ci] = c.value;
    }
    return { rows, cols, matrix, min: mn === Infinity ? 0 : mn, max: mx === -Infinity ? 1 : mx };
  }, [data]);

  if (data.length === 0) return <p className="text-sm text-slate-500">无数据</p>;

  const colorOf = (value: number | null) => {
    if (value === null || !Number.isFinite(value)) return "#f1f5f9";
    if (max === min) return "#bfdbfe";
    const t = (value - min) / (max - min);
    // Viridis-like: dark blue → teal → green → yellow
    const stops: Array<[number, [number, number, number]]> = [
      [0, [68, 1, 84]],
      [0.25, [59, 82, 139]],
      [0.5, [33, 145, 140]],
      [0.75, [94, 201, 98]],
      [1, [253, 231, 37]],
    ];
    let lo = stops[0], hi = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i++) {
      if (t >= stops[i][0] && t <= stops[i + 1][0]) { lo = stops[i]; hi = stops[i + 1]; break; }
    }
    const span = hi[0] - lo[0] || 1;
    const k = (t - lo[0]) / span;
    const r = Math.round(lo[1][0] + (hi[1][0] - lo[1][0]) * k);
    const g = Math.round(lo[1][1] + (hi[1][1] - lo[1][1]) * k);
    const b = Math.round(lo[1][2] + (hi[1][2] - lo[1][2]) * k);
    return `rgb(${r},${g},${b})`;
  };

  return (
    <div style={{ height }} className="overflow-auto">
      <div className="mb-2 text-xs text-slate-500">{valueLabel} 范围: {min.toFixed(4)} — {max.toFixed(4)}</div>
      <table className="border-collapse text-xs">
        <thead>
          <tr>
            <th className="border border-line bg-panel px-2 py-1 text-xs text-slate-500">{rowLabel} \ {colLabel}</th>
            {cols.map((c) => (
              <th key={String(c)} className="border border-line bg-panel px-2 py-1 font-mono text-slate-700">{String(c)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={String(r)}>
              <th className="border border-line bg-panel px-2 py-1 font-mono text-slate-700">{String(r)}</th>
              {cols.map((c, ci) => {
                const v = matrix[ri][ci];
                const bg = colorOf(v);
                const text = v !== null && (Math.max(...bg.match(/\d+/g)!.map(Number).slice(0, 3)) > 200) ? "#1e293b" : "#ffffff";
                return (
                  <td key={String(c)} className="border border-line px-2 py-1 text-center font-mono" style={{ background: bg, color: text, minWidth: 56 }}
                    title={v === null ? "—" : `${rowLabel}=${r}, ${colLabel}=${c}: ${formatValue(v)}`}>
                    {v === null ? "—" : formatValue(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
