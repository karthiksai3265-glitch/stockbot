import Link from "next/link";
import type { Rec, Source } from "@/lib/types";

export function DemoBanner({ source, what }: { source: Source; what: string }) {
  if (source === "live") return null;
  return (
    <div className="banner">
      <b>Sample data.</b> {what} is showing made-up examples because the model for it isn't built yet (or the API isn't
      running). Do not trade on this.
    </div>
  );
}

export const Badge = ({ action }: { action: "BUY" | "SELL" }) => (
  <span className={`badge ${action === "BUY" ? "buy" : "sell"}`}>{action}</span>
);

export const Confidence = ({ v }: { v: number }) => (
  <span title={`${Math.round(v * 100)}% signal strength (not a win probability)`} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
    <span className="bar"><i style={{ width: `${v * 100}%` }} /></span>{Math.round(v * 100)}%
  </span>
);

export function RecTable({ recs }: { recs: Rec[] }) {
  const cur = (r: Rec) => (r.market === "ASX200" ? "A$" : r.market === "NIFTY50" ? "₹" : "$");
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr><th>Stock</th><th>Action</th><th>Entry</th><th>Target</th><th>Stop-loss</th><th>Size</th><th title="How strong the combined signal is. Not a probability of winning.">Signal strength</th><th>Why</th></tr>
        </thead>
        <tbody>
          {recs.map((r) => (
            <tr key={r.id}>
              <td><Link className="sym" href={`/stock/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link><div className="muted">{r.name}</div></td>
              <td><Badge action={r.action} /><div className="muted">{r.window}</div></td>
              <td>{cur(r)}{r.entry.toFixed(2)}</td>
              <td className={r.action === "BUY" ? "pos" : "neg"}>{cur(r)}{r.target.toFixed(2)}</td>
              <td>{cur(r)}{r.stop.toFixed(2)}</td>
              <td>{r.sizePct}%</td>
              <td><Confidence v={r.confidence} /></td>
              <td><ul className="reasons">{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
