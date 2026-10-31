import Link from "next/link";
import { Badge, DemoBanner } from "@/components/Parts";
import { fmt, getHistory } from "@/lib/data";

export const dynamic = "force-dynamic";

export default async function History() {
  const { data, source } = await getHistory();
  return (
    <>
      <h1>Recommendation history</h1>
      <p className="sub">Every recommendation is logged and graded against what the stock actually did, including the losses.</p>
      <DemoBanner source={source} what="History" />
      <div className="tablewrap">
        <table>
          <thead><tr><th>Date</th><th>Stock</th><th>Action</th><th>Entry</th><th>Target / Stop</th><th>Strength</th><th>Result</th></tr></thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.id}>
                <td>{r.created.slice(0, 10)}</td>
                <td><Link className="sym" href={`/stock/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link></td>
                <td><Badge action={r.action} /></td>
                <td>{r.entry.toFixed(2)}</td>
                <td>{r.target.toFixed(2)} / {r.stop.toFixed(2)}</td>
                <td>{Math.round(r.confidence * 100)}%</td>
                <td>
                  {r.outcome ? (
                    <><span className={`badge ${r.outcome.status === "WIN" ? "buy" : r.outcome.status === "LOSS" ? "sell" : "open"}`}>{r.outcome.status}</span>{" "}
                    <span className={r.outcome.pnlPct >= 0 ? "pos" : "neg"}>{fmt.pct(r.outcome.pnlPct)}</span></>
                  ) : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
