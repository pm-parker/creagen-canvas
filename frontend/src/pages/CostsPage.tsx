import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

interface CostStats {
  daily_cost: number;
  monthly_cost: number;
  total_cost: number;
  daily_breakdown: Array<{ date: string; cost: number; count: number }>;
  model_breakdown: Array<{ model: string; cost: number; count: number }>;
  month_completed_count: number;
}

const fmt = (n: number) => `¥${n.toFixed(2)}`;
/** 有调用但金额为 0 → 价格表未覆盖该模型，诚实展示而非误导性 ¥0.00。 */
const FREE_MODELS = ["Kwai-Kolors/Kolors"];
const fmtCost = (n: number, calls: number, model?: string) => {
  if (calls > 0 && n === 0) return model && FREE_MODELS.includes(model) ? "免费" : "未计价";
  return fmt(n);
};

export function CostsPage() {
  const stats = useQuery({
    queryKey: ["costs", "stats"],
    queryFn: () => apiFetch<CostStats>("/api/costs/stats"),
    refetchInterval: 30_000,
  });

  if (stats.isLoading) return <p className="p-8 text-sm text-zinc-500">加载中…</p>;
  if (stats.isError) return <p className="p-8 text-sm text-red-600">成本数据加载失败</p>;
  const s = stats.data!;
  const maxDaily = Math.max(...s.daily_breakdown.map((b) => b.cost), 0.001);
  const maxModel = Math.max(...s.model_breakdown.map((b) => b.cost), 0.001);

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white px-8 py-6">
        <h1 className="text-xl font-semibold">成本看板</h1>
        <p className="mt-1 text-sm text-zinc-500">你的生成消费统计（当月/今日/按模型）</p>
      </header>
      <main className="mx-auto max-w-5xl space-y-6 p-8">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[
            ["总消费", fmt(s.total_cost)],
            ["本月", fmt(s.monthly_cost)],
            ["今日", fmt(s.daily_cost)],
            ["本月完成", `${s.month_completed_count} 次`],
          ].map(([label, value]) => (
            <div key={label} className="rounded-2xl border border-zinc-200 bg-white p-5">
              <p className="text-xs text-zinc-500">{label}</p>
              <p className="mt-1 text-2xl font-semibold text-zinc-900">{value}</p>
            </div>
          ))}
        </div>

        <section className="rounded-2xl border border-zinc-200 bg-white p-5">
          <h2 className="mb-4 text-sm font-medium text-zinc-500">近 7 日消费</h2>
          <div className="flex h-40 items-end gap-3">
            {s.daily_breakdown.map((b) => (
              <div key={b.date} className="flex flex-1 flex-col items-center gap-1">
                <span className="text-xs text-zinc-500">{b.cost > 0 ? fmt(b.cost) : ""}</span>
                <div
                  className="w-full rounded-t-md bg-zinc-900/85"
                  style={{ height: `${Math.max(4, (b.cost / maxDaily) * 120)}px` }}
                  title={`${b.date}: ${fmt(b.cost)} (${b.count} 次)`}
                />
                <span className="text-xs text-zinc-400">{b.date}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-zinc-200 bg-white p-5">
          <h2 className="mb-4 text-sm font-medium text-zinc-500">本月按模型分布</h2>
          {s.model_breakdown.length === 0 ? (
            <p className="text-sm text-zinc-400">本月暂无消费记录</p>
          ) : (
            <div className="space-y-3">
              {s.model_breakdown.map((b) => (
                <div key={b.model}>
                  <div className="mb-1 flex justify-between text-xs">
                    <span className="truncate text-zinc-700">{b.model}（{b.count} 次）</span>
                    <span className="text-zinc-500">{fmtCost(b.cost, b.count, b.model)}</span>
                  </div>
                  <div className="h-2 rounded-full bg-zinc-100">
                    <div
                      className="h-2 rounded-full bg-zinc-900/80"
                      style={{ width: `${Math.max(3, (b.cost / maxModel) * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
