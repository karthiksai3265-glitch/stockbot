import Link from "next/link";
import { EquityChart } from "@/components/Charts";
import { DemoBanner } from "@/components/Parts";
import { fmt, getPortfolio } from "@/lib/data";

export const dynamic = "force-dynamic";

export default async function PortfolioPage() {
  const { data: p, source } = await getPortfolio();
  const invested = p.positions.reduce((a, x) => a + x.qty * x.last, 0);
  const cost = p.positions.reduce((a, x) => a + x.qty * x.avgPrice, 0);
  const total = p.cash + invested;
  const pnl = invested - cost;

  return (
    <>
      <h1>Paper portfolio</h1>
      <p className="sub">A simulated portfolio (fake money) that follows the model's recommendations, so you can see how they would have performed before risking real money.</p>
      <DemoBanner source={source} what="The portfolio" />
      <div className="grid g4">
        <div className="card"><div className="k">Total value</div><div className="v">{fmt.money(total)}</div></div>
        <div className="card"><div className="k">Cash</div><div className="v">{fmt.money(p.cash)}</div></div>
        <div className="card"><div className="k">Open P&L</div><div className={`v ${pnl >= 0 ? "pos" : "neg"}`}>{fmt.money(pnl)}</div></div>
        <div className="card"><div className="k">Positions</div><div className="v">{p.positions.length}</div></div>
      </div>
      <h2>Value over time</h2>
      <EquityChart data={p.equity} />
      <h2>Positions</h2>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Stock</th><th>Side</th><th>Qty</th><th>Avg price</th><th>Last</th><th>P&L</th></tr></thead>
          <tbody>
            {p.positions.map((x) => {
              const gain = (x.last - x.avgPrice) * x.qty;
              return (
                <tr key={x.symbol}>
                  <td><Link className="sym" href={`/stock/${encodeURIComponent(x.symbol)}`}>{x.symbol}</Link></td>
                  <td>{x.qty >= 0 ? "Long" : "Short"}</td><td>{Math.abs(x.qty)}</td><td>{x.avgPrice.toFixed(2)}</td><td>{x.last.toFixed(2)}</td>
                  <td className={gain >= 0 ? "pos" : "neg"}>{fmt.money(gain)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h2>Risk limits</h2>
      <div className="card muted">Max 5% of the portfolio per stock · max 25% per sector · at least 10% kept in cash · max 20 positions · about 0.5% of the portfolio risked per trade (by sizing to the stop-loss) · no new entries on a day the portfolio is down 5% or more · kill switch available. Long-only; currency moves between markets are ignored.</div>
    </>
  );
}
