import type { ReactNode } from "react";

export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card min-w-0 p-3 sm:p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-2 break-words text-xl font-semibold sm:text-2xl">{value}</div>
    </div>
  );
}

export function IconButton({ label, icon, onClick, disabled = false }: { label: string; icon: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="focus-ring flex w-full items-center justify-center gap-1 rounded border border-line px-2.5 py-1.5 text-sm hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto" onClick={onClick} title={label} disabled={disabled}>
      {icon}
      {label}
    </button>
  );
}

export function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="break-words font-medium">{value}</div>
    </div>
  );
}

export function Segmented({ value, options, onChange }: { value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <div className="flex w-full max-w-full overflow-x-auto rounded border border-line bg-white p-1 sm:inline-flex sm:w-auto">
      {options.map((option) => (
        <button
          key={option}
          className={`focus-ring shrink-0 rounded px-3 py-1.5 text-sm ${value === option ? "bg-ink text-white" : "text-slate-600 hover:bg-panel"}`}
          onClick={() => onChange(option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
