import { useCallback, useEffect, useMemo, useState } from "react";
import Editor from "@monaco-editor/react";
import { api } from "../api";
import type { StrategyFileInfo } from "../types";

const SAMPLE_CONTENT = `"""My new CTA strategy."""

from __future__ import annotations

from omega_quant.core.strategy_template import CtaStrategyTemplate
from omega_quant.core.cta_utility import ArrayManager
from omega_quant.core.objects import BarData


class MyStrategy(CtaStrategyTemplate):
    """Describe your strategy here."""

    author = "me"
    # 大类: "stock" (美股) 或 "option" (美股期权)
    category = "stock"
    # 周期 tag: tick / 1m / 5m / 15m / 30m / 1h / 1d / 1w
    tags = ["1d"]
    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["inited", "trading", "pos", "fast_ma", "slow_ma"]

    fast_window: int = 5
    slow_window: int = 20
    fixed_size: float = 1.0

    fast_ma: float = 0.0
    slow_ma: float = 0.0

    def on_init(self) -> None:
        self.am = ArrayManager()
        self.inited = True

    def on_start(self) -> None:
        self.trading = True

    def on_stop(self) -> None:
        self.trading = False

    def on_tick(self, tick) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        self.fast_ma = float(self.am.sma(int(self.fast_window)))
        self.slow_ma = float(self.am.sma(int(self.slow_window)))
        if not self.trading:
            return
        if self.fast_ma > self.slow_ma and self.pos <= 0:
            self.buy(bar.close_price, self.fixed_size)
        elif self.fast_ma < self.slow_ma and self.pos >= 0:
            self.sell(bar.close_price, self.fixed_size)
`;

const ALL_TAG_OPTIONS = ["tick", "1m", "5m", "15m", "30m", "1h", "1d", "1w"] as const;
const TAG_LABEL: Record<string, string> = {
  tick: "Tick",
  "1m": "1 分钟",
  "5m": "5 分钟",
  "15m": "15 分钟",
  "30m": "30 分钟",
  "1h": "小时线",
  "1d": "日线",
  "1w": "周线",
};
const CATEGORY_LABEL: Record<string, string> = {
  stock: "股票策略",
  option: "期权策略",
  "": "未分类",
};

type SelectedKey = { name: string; source: "user" | "builtin" } | null;

export default function StrategyFilesPage() {
  const [userFiles, setUserFiles] = useState<StrategyFileInfo[]>([]);
  const [builtinFiles, setBuiltinFiles] = useState<StrategyFileInfo[]>([]);
  const [selected, setSelected] = useState<SelectedKey>(null);
  const [content, setContent] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<"all" | "stock" | "option" | "uncategorized">("all");
  const [tagFilter, setTagFilter] = useState<string>("all");

  const isReadonly = selected?.source === "builtin";

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [user, builtin] = await Promise.all([
        api.ctaStrategyFiles(),
        api.ctaStrategyBuiltins(),
      ]);
      setUserFiles(user);
      setBuiltinFiles(builtin);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const openFile = useCallback(
    async (key: SelectedKey) => {
      if (!key) return;
      if (dirty && !confirm("当前文件有未保存的修改，确认放弃？")) return;
      setBusy(true);
      setError(null);
      try {
        if (key.source === "builtin") {
          const { content: c } = await api.readCtaStrategyBuiltin(key.name);
          setSelected(key);
          setContent(c);
          setDirty(false);
        } else {
          const { content: c } = await api.readCtaStrategyFile(key.name);
          setSelected(key);
          setContent(c);
          setDirty(false);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [dirty],
  );

  const handleSave = useCallback(async () => {
    if (!selected || selected.source !== "user") return;
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await api.writeCtaStrategyFile(selected.name, content);
      setDirty(false);
      if (r.error) {
        setError(`已保存但加载失败: ${r.error}`);
      } else {
        setInfo(`✓ 已保存并热加载 (${r.classes.length} 个策略类: ${r.classes.join(", ") || "无"})`);
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [selected, content, refresh]);

  const handleNew = useCallback(async () => {
    const name = newName.trim();
    if (!name) {
      setError("请输入文件名");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.writeCtaStrategyFile(name, SAMPLE_CONTENT);
      setNewName("");
      setSelected({ name: r.name, source: "user" });
      setContent(SAMPLE_CONTENT);
      setDirty(false);
      setInfo(`✓ 新建 ${r.filename} 成功`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [newName, refresh]);

  const handleDelete = useCallback(
    async (name: string) => {
      if (!confirm(`确认删除 ${name}.py？此操作不可恢复。`)) return;
      setBusy(true);
      setError(null);
      try {
        await api.deleteCtaStrategyFile(name);
        if (selected?.source === "user" && selected.name === name) {
          setSelected(null);
          setContent("");
          setDirty(false);
        }
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [selected, refresh],
  );

  const handleReload = useCallback(async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const result = await api.reloadCtaStrategyFiles();
      const totalClasses = result.reduce((sum, f) => sum + f.classes.length, 0);
      const errs = result.filter((f) => f.error);
      setInfo(
        `✓ 重载完成: ${result.length} 个用户文件, ${totalClasses} 个策略类${
          errs.length ? `, ${errs.length} 个文件有错误` : ""
        }`,
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleDownload = useCallback(() => {
    if (!selected) return;
    const blob = new Blob([content], { type: "text/x-python" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${selected.name}.py`;
    a.click();
    URL.revokeObjectURL(url);
  }, [selected, content]);

  const handleCopyAsTemplate = useCallback(() => {
    if (!selected) return;
    // 把内置源码作为新文件的草稿：转给新建框
    const suggestedName = `${selected.name.toLowerCase()}_copy`;
    setNewName(suggestedName);
    // 把内容拷到剪贴板, 用户粘贴到自己新建的文件里
    navigator.clipboard?.writeText(content).then(
      () => setInfo(`✓ 已复制源码到剪贴板。在左侧"新文件名"填入名字 (默认 ${suggestedName}) 创建后粘贴`),
      () => setInfo("已生成新文件名，但剪贴板复制失败 — 可手动复制源码"),
    );
  }, [selected, content]);

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const text = await file.text();
      const stem = file.name.replace(/\.py$/i, "");
      setBusy(true);
      setError(null);
      try {
        await api.writeCtaStrategyFile(stem, text);
        setInfo(`✓ 导入 ${file.name} 成功`);
        await refresh();
        setSelected({ name: stem, source: "user" });
        setContent(text);
        setDirty(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        e.target.value = "";
      }
    },
    [refresh],
  );

  const filterFile = useCallback(
    (f: StrategyFileInfo): boolean => {
      if (categoryFilter !== "all") {
        if (categoryFilter === "uncategorized" && f.category) return false;
        if (categoryFilter === "stock" && f.category !== "stock") return false;
        if (categoryFilter === "option" && f.category !== "option") return false;
      }
      if (tagFilter !== "all" && !f.tags.includes(tagFilter)) return false;
      return true;
    },
    [categoryFilter, tagFilter],
  );

  const grouped = useMemo(() => {
    const all = [...builtinFiles, ...userFiles].filter(filterFile);
    const groups: Record<string, StrategyFileInfo[]> = { stock: [], option: [], "": [] };
    for (const f of all) {
      const key = f.category in groups ? f.category : "";
      groups[key].push(f);
    }
    return groups;
  }, [userFiles, builtinFiles, filterFile]);

  return (
    <div className="space-y-5">
      <section className="panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">CTA 策略源码</div>
            <h2 className="mt-1 text-xl font-semibold">Python 策略文件 (vnpy 风格)</h2>
            <p className="mt-1 text-sm text-slate-500">
              写普通的 Python 子类继承 <code className="rounded bg-panel px-1">CtaStrategyTemplate</code>，保存后即时热加载到回测器与实盘 CTA 引擎。
              在类上加 <code className="rounded bg-panel px-1">category = "stock" / "option"</code> 与 <code className="rounded bg-panel px-1">tags = ["1d"]</code> 即可分类。
              内置策略 (Donchian / MA Cross / TickMomentum 等) 显示为只读，可通过"复制为模板"派生自己的版本。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <label className="focus-ring cursor-pointer rounded border border-line px-3 py-1.5 text-sm hover:bg-panel">
              导入 .py
              <input type="file" accept=".py,text/x-python" className="hidden" onChange={handleUpload} />
            </label>
            <button
              className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50"
              disabled={busy}
              onClick={handleReload}
            >
              重载全部
            </button>
          </div>
        </div>
        {error && (
          <p className="mt-3 rounded border border-danger/30 bg-red-50 px-3 py-2 text-sm text-danger whitespace-pre-wrap">{error}</p>
        )}
        {info && <p className="mt-3 rounded border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{info}</p>}
      </section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[300px_1fr]">
        <section className="panel p-3">
          <div className="mb-3 flex items-center gap-2">
            <input
              className="focus-ring flex-1 rounded border border-line px-3 py-1.5 text-sm"
              placeholder="新文件名"
              value={newName}
              onChange={(e) => setNewName(e.target.value.trim())}
            />
            <button
              className="focus-ring rounded border border-accent bg-accent px-2.5 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-50"
              disabled={busy || !newName}
              onClick={handleNew}
            >
              新建
            </button>
          </div>

          <div className="mb-3 grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs text-slate-500">
              大类
              <select
                className="focus-ring rounded border border-line px-2 py-1 text-sm"
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value as typeof categoryFilter)}
              >
                <option value="all">全部</option>
                <option value="stock">股票</option>
                <option value="option">期权</option>
                <option value="uncategorized">未分类</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-500">
              周期
              <select
                className="focus-ring rounded border border-line px-2 py-1 text-sm"
                value={tagFilter}
                onChange={(e) => setTagFilter(e.target.value)}
              >
                <option value="all">全部</option>
                {ALL_TAG_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {TAG_LABEL[t] ?? t}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {(["stock", "option", ""] as const).map((cat) => {
            const list = grouped[cat] ?? [];
            if (list.length === 0) return null;
            return (
              <div key={cat || "uncat"} className="mb-3">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  {CATEGORY_LABEL[cat] ?? "未分类"} <span className="text-slate-400">({list.length})</span>
                </div>
                <div className="space-y-1">
                  {list.map((f) => {
                    const key = `${f.source_type}:${f.name}`;
                    const isSelected = selected && selected.source === f.source_type && selected.name === f.name;
                    return (
                      <div
                        key={key}
                        className={`group rounded border px-2 py-1.5 text-sm ${
                          isSelected ? "border-accent bg-accent/10" : "border-line hover:bg-panel"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <button
                            className="min-w-0 flex-1 truncate text-left"
                            onClick={() => openFile({ name: f.name, source: f.source_type })}
                            title={f.filename}
                          >
                            {f.filename}
                          </button>
                          {f.source_type === "user" ? (
                            <button className="text-xs text-red-600 hover:underline" onClick={() => handleDelete(f.name)}>
                              删除
                            </button>
                          ) : (
                            <span className="rounded bg-slate-200 px-1.5 text-[10px] text-slate-700">只读</span>
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1 text-xs">
                          {f.tags.length > 0 ? (
                            f.tags.map((t) => (
                              <span key={t} className="rounded bg-sky-100 px-1.5 text-sky-800">
                                {TAG_LABEL[t] ?? t}
                              </span>
                            ))
                          ) : (
                            <span className="text-slate-400">无周期 tag</span>
                          )}
                          {f.error ? (
                            <span className="rounded bg-red-100 px-1.5 text-red-700">加载错误</span>
                          ) : f.classes.length > 0 ? (
                            f.classes.map((c) => (
                              <span key={c} className="rounded bg-emerald-100 px-1.5 text-emerald-800">
                                {c}
                              </span>
                            ))
                          ) : (
                            <span className="text-slate-400">无策略类</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {Object.values(grouped).every((list) => list.length === 0) && (
            <p className="py-4 text-center text-sm text-slate-500">没有匹配当前筛选条件的策略</p>
          )}
        </section>

        <section className="panel p-3">
          {!selected ? (
            <div className="flex h-96 items-center justify-center text-sm text-slate-500">从左侧选择文件，或新建一个开始</div>
          ) : (
            <div className="flex h-full min-h-[480px] flex-col gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="font-mono text-sm">
                    {selected.name}
                    {selected.source === "user" ? ".py" : ""}{" "}
                    {dirty && <span className="text-amber-700">●未保存</span>}
                    {isReadonly && (
                      <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-[10px] text-slate-700">内置 · 只读</span>
                    )}
                  </div>
                  <div className="text-xs text-slate-500">
                    {isReadonly
                      ? "内置策略源码不可编辑，可"
                      : "保存后会立即热加载，新的策略类立刻可在回测/实盘下拉框选到"}
                    {isReadonly && (
                      <button className="ml-1 text-accent hover:underline" onClick={handleCopyAsTemplate}>
                        复制为模板
                      </button>
                    )}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    className="focus-ring rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50"
                    disabled={busy}
                    onClick={handleDownload}
                  >
                    下载
                  </button>
                  {!isReadonly && (
                    <button
                      className="focus-ring rounded border border-accent bg-accent px-4 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
                      disabled={busy || !dirty}
                      onClick={handleSave}
                    >
                      保存并热加载
                    </button>
                  )}
                </div>
              </div>
              <div className="h-[60vh] overflow-hidden rounded border border-line">
                <Editor
                  height="100%"
                  defaultLanguage="python"
                  value={content}
                  onChange={(value) => {
                    if (isReadonly) return;
                    setContent(value ?? "");
                    setDirty(true);
                  }}
                  theme="vs-dark"
                  options={{
                    fontSize: 13,
                    minimap: { enabled: false },
                    scrollBeyondLastLine: false,
                    tabSize: 4,
                    insertSpaces: true,
                    automaticLayout: true,
                    wordWrap: "on",
                    readOnly: isReadonly,
                  }}
                  onMount={(editor, monaco) => {
                    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
                      handleSave();
                    });
                  }}
                />
              </div>
              <p className="mt-2 text-xs text-slate-500">
                提示：⌘ + S / Ctrl + S 保存。在类上写 <code className="rounded bg-panel px-1">category</code> /{" "}
                <code className="rounded bg-panel px-1">tags</code> 即可被识别为对应大类/周期。
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
