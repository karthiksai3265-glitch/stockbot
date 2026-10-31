import { WinRateChart } from "@/components/Charts";
import { DemoBanner } from "@/components/Parts";
import { getPerformance } from "@/lib/data";

export const dynamic = "force-dynamic";

export default async function Performance() {
  const { data, source } = await getPerformance();
  return (
    <>
      <h1>Model performance</h1>
      <p className="sub">How often past recommendations were right, and the history of model versions. A new version only replaces the current one if it beats it in backtesting and paper trading.</p>
      <DemoBanner source={source} what="Performance" />
      <h2>Win rate by month</h2>
      <WinRateChart data={data.accuracy} />
      <p className="muted">The dashed line is 50%. A win rate near 50% is not an edge by itself; look at average gain vs average loss too.</p>
      <h2>Model versions</h2>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Version</th><th>Promoted</th><th>Win rate</th><th>Backtest return</th><th>What changed</th></tr></thead>
          <tbody>
            {data.versions.map((v) => (
              <tr key={v.version}><td className="sym">{v.version}</td><td>{v.promoted}</td><td>{Math.round(v.winRate * 100)}%</td><td className={v.backtestReturnPct >= 0 ? "pos" : "neg"}>{v.backtestReturnPct.toFixed(1)}%</td><td className="muted">{v.note}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Learning log</h2>
      <p className="muted">Each week the model tries small changes. It tunes on older data and is judged once on newer data it has not seen. Most attempts are rejected, which is intended.</p>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Date</th><th>Change tried</th><th>Decision</th><th>Reason</th></tr></thead>
          <tbody>
            {(data.log ?? []).map((l, i) => (
              <tr key={i}>
                <td>{l.date}</td><td>{l.change}</td>
                <td><span className={`badge ${l.decision === "promoted" ? "buy" : l.decision === "rejected" || l.decision === "warning" || l.decision === "rollback" ? "sell" : "open"}`}>{l.decision}</span></td>
                <td className="muted">{l.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
