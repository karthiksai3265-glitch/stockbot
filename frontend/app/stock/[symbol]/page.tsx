import { Badge, DemoBanner } from "@/components/Parts";
import { PriceChart } from "@/components/Charts";
import { TradingViewChart } from "@/components/TradingView";
import { fmt, getBars, getFundamentals, getNews, getRecommendations } from "@/lib/data";

export const dynamic = "force-dynamic";

export default async function Stock({ params }: { params: Promise<{ symbol: string }> }) {
  const symbol = decodeURIComponent((await params).symbol).toUpperCase();
  const [bars, fund, news, recs] = await Promise.all([getBars(symbol), getFundamentals(symbol), getNews(symbol), getRecommendations()]);
  const rec = recs.data.find((r) => r.symbol === symbol);
  const last = bars.data[bars.data.length - 1];
  const prev = bars.data[bars.data.length - 2];
  const chg = last && prev ? ((last.close - prev.close) / prev.close) * 100 : 0;
  const anySample = [bars, fund, news].some((x) => x.source === "demo");

  return (
    <>
      <h1>{symbol} {rec && <Badge action={rec.action} />}</h1>
      <p className="sub">{rec?.name ?? ""} {last && <>· Last close {last.close.toFixed(2)} <span className={chg >= 0 ? "pos" : "neg"}>{fmt.pct(chg)}</span></>}</p>
      {anySample && <DemoBanner source="demo" what="Parts of this page" />}
      <PriceChart data={bars.data} entry={rec?.entry} target={rec?.target} stop={rec?.stop} />

      <h2>Interactive chart (TradingView)</h2>
      <TradingViewChart symbol={symbol} />

      {rec && (
        <>
          <h2>Why the model likes this</h2>
          <div className="card">
            <ul className="reasons" style={{ marginTop: 0 }}>{rec.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
            <p className="muted" style={{ marginBottom: 0 }}>
              Signal scores (−1 to +1): technical {rec.scores.technical.toFixed(1)} · fundamentals {rec.scores.fundamental.toFixed(1)} · news {rec.scores.sentiment.toFixed(1)}
            </p>
          </div>
        </>
      )}

      <h2>Company financials (quarterly)</h2>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Quarter end</th><th>Sales</th><th>Net income</th><th>Total debt</th><th>Free cash flow</th></tr></thead>
          <tbody>
            {[...fund.data].reverse().map((f) => (
              <tr key={f.period_end}><td>{f.period_end.slice(0, 10)}</td><td>{fmt.big(f.revenue)}</td><td>{fmt.big(f.net_income)}</td><td>{fmt.big(f.total_debt)}</td><td>{fmt.big(f.free_cash_flow)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Latest news</h2>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Headline</th><th>Source</th><th>Sentiment</th></tr></thead>
          <tbody>
            {news.data.map((n, i) => (
              <tr key={i}>
                <td>{n.url ? <a href={n.url} target="_blank" rel="noreferrer" className="sym">{n.title}</a> : n.title}</td>
                <td className="muted">{n.publisher ?? ""}</td>
                <td className={(n.sentiment ?? 0) > 0.15 ? "pos" : (n.sentiment ?? 0) < -0.15 ? "neg" : "muted"}>{n.sentiment == null ? "–" : n.sentiment.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
