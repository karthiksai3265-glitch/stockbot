"use client";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const axis = { stroke: "var(--muted)", fontSize: 12 };
const tip = { contentStyle: { background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--text)" } };

export function PriceChart({ data, entry, target, stop }: { data: { date: string; close: number }[]; entry?: number; target?: number; stop?: number }) {
  const ma = data.map((d, i) => {
    const w = data.slice(Math.max(0, i - 49), i + 1);
    return { ...d, ma50: i >= 49 ? w.reduce((a, b) => a + b.close, 0) / w.length : null };
  });
  return (
    <div style={{ height: 320 }} className="card">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={ma} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="date" {...axis} minTickGap={50} tickFormatter={(d) => d.slice(5)} />
          <YAxis {...axis} domain={["auto", "auto"]} width={52} />
          <Tooltip {...tip} formatter={(v: number) => v.toFixed(2)} />
          <Line type="monotone" dataKey="close" stroke="var(--accent)" dot={false} strokeWidth={2} name="Close" />
          <Line type="monotone" dataKey="ma50" stroke="var(--amber)" dot={false} strokeWidth={1.5} strokeDasharray="4 3" name="50-day avg" connectNulls />
          {entry && <ReferenceLine y={entry} stroke="var(--muted)" label={{ value: "entry", fill: "var(--muted)", fontSize: 11 }} />}
          {target && <ReferenceLine y={target} stroke="var(--green)" strokeDasharray="3 3" label={{ value: "target", fill: "var(--green)", fontSize: 11 }} />}
          {stop && <ReferenceLine y={stop} stroke="var(--red)" strokeDasharray="3 3" label={{ value: "stop", fill: "var(--red)", fontSize: 11 }} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function EquityChart({ data }: { data: { date: string; value: number }[] }) {
  return (
    <div style={{ height: 280 }} className="card">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="date" {...axis} minTickGap={40} />
          <YAxis {...axis} domain={["auto", "auto"]} width={60} tickFormatter={(v) => `${Math.round(v / 1000)}k`} />
          <Tooltip {...tip} formatter={(v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 })} />
          <Area type="monotone" dataKey="value" stroke="var(--accent)" fill="var(--accent)" fillOpacity={0.15} strokeWidth={2} name="Portfolio value" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WinRateChart({ data }: { data: { month: string; winRate: number }[] }) {
  const rows = data.map((d) => ({ ...d, pct: Math.round(d.winRate * 100) }));
  return (
    <div style={{ height: 240 }} className="card">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="month" {...axis} />
          <YAxis {...axis} domain={[0, 100]} width={40} tickFormatter={(v) => `${v}%`} />
          <Tooltip {...tip} formatter={(v: number) => `${v}%`} />
          <ReferenceLine y={50} stroke="var(--muted)" strokeDasharray="4 4" />
          <Bar dataKey="pct" fill="var(--accent)" radius={[4, 4, 0, 0]} name="Win rate" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
