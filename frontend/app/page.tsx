import { DemoBanner, RecTable } from "@/components/Parts";
import { getHistory, getRecommendations } from "@/lib/data";

export const dynamic = "force-dynamic";

export default async function Today() {
  const [{ data: recs, source }, { data: hist }] = await Promise.all([getRecommendations(), getHistory()]);
  const closed = hist.filter((h) => h.outcome && h.outcome.status !== "OPEN");
  const wins = closed.filter((h) => h.outcome!.status === "WIN").length;
  const buys = recs.filter((r) => r.action === "BUY").length;

  return (
    <>
      <h1>Today's recommendations</h1>
      <p className="sub">Place these yourself in your broker app. Each one lists entry, target, stop-loss and suggested size.</p>
      <DemoBanner source={source} what="Today's list" />
      <div className="grid g4">
        <div className="card"><div className="k">Recommendations</div><div className="v">{recs.length}</div></div>
        <div className="card"><div className="k">Buy / Sell</div><div className="v">{buys} / {recs.length - buys}</div></div>
        <div className="card"><div className="k">Recent win rate</div><div className="v">{closed.length ? `${Math.round((wins / closed.length) * 100)}%` : "–"}</div><div className="k">{closed.length} closed calls</div></div>
        <div className="card"><div className="k">Next update</div><div className="v">Pre-open & post-close</div></div>
      </div>
      <h2>Recommendations</h2>
      <RecTable recs={recs} />
    </>
  );
}
