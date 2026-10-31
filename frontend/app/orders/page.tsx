import Link from "next/link";
import { HaltButton, TicketActions } from "@/components/OrderControls";
import { fmt, getExecStatus, getTickets } from "@/lib/data";

export const dynamic = "force-dynamic";

const modeLabel = { manual: "Manual", paper: "Paper money", live: "LIVE MONEY" } as const;

export default async function Orders() {
  const [st, tickets] = await Promise.all([getExecStatus(), getTickets()]);

  if (!st) {
    return (
      <>
        <h1>Orders</h1>
        <div className="banner">The StockBot API is not reachable, so orders cannot be shown. Start it with <code>uvicorn app.api:app --port 8000</code>.</div>
      </>
    );
  }

  const pending = tickets.filter((t) => t.status === "PENDING");
  const past = tickets.filter((t) => t.status !== "PENDING");
  const canApprove = st.web_approval_enabled && st.mode !== "manual" && !st.halted;

  return (
    <>
      <h1>Orders</h1>
      <p className="sub">One-tap approval. Nothing is sent to your broker until you approve that specific order, and every limit below is checked again at that moment.</p>

      {st.mode === "manual" ? (
        <div className="banner">
          <b>Manual mode.</b> {st.message} Recommendations on the Today page are still the place to read what to do.
        </div>
      ) : (
        <div className="grid g4">
          <div className="card"><div className="k">Account</div><div className="v">{st.connected ? modeLabel[st.mode] : "Not connected"}</div>
            {st.mode === "live" && <div className="neg">Real money. Orders are real.</div>}
            {st.message && <div className="muted">{st.message}</div>}</div>
          <div className="card"><div className="k">Equity / cash</div><div className="v">{st.equity != null ? fmt.money(st.equity) : "–"}</div><div className="muted">{st.cash != null ? `${fmt.money(st.cash)} cash` : ""}</div></div>
          <div className="card"><div className="k">Approved today</div><div className="v">{st.today.orders} / {st.limits.max_daily_orders}</div><div className="muted">{fmt.money(st.today.usd)} of {fmt.money(st.limits.max_daily_usd)}</div></div>
          <div className="card"><div className="k">Largest single order</div><div className="v">{fmt.money(st.limits.max_order_usd)}</div><div className="muted">Tickets expire after {st.limits.ticket_ttl_hours} hours</div></div>
        </div>
      )}

      {st.mode !== "manual" && (
        <p style={{ marginTop: 14 }}>
          <HaltButton halted={st.halted} canApprove={st.web_approval_enabled} />
          {st.halted && <span className="neg" style={{ marginLeft: 10 }}>Kill switch is ON: no orders can be approved.</span>}
          {!st.web_approval_enabled && <span className="muted" style={{ marginLeft: 10 }}>Website approval is off. Set STOCKBOT_APPROVE_TOKEN (backend and website) or approve with <code>python -m app.execution approve ID</code>.</span>}
        </p>
      )}

      <h2>Waiting for your approval</h2>
      {pending.length === 0 ? (
        <div className="card muted">Nothing waiting. New tickets appear after each daily run when a broker is set.</div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead><tr><th>Stock</th><th>Order</th><th>Entry</th><th>Stop / Target</th><th>Cost</th><th></th></tr></thead>
            <tbody>
              {pending.map((t) => (
                <tr key={t.id}>
                  <td><Link className="sym" href={`/stock/${encodeURIComponent(t.symbol)}`}>{t.symbol}</Link></td>
                  <td><span className={`badge ${t.side === "buy" ? "buy" : "sell"}`}>{t.side === "buy" ? "BUY" : "SELL (close)"}</span> {t.qty} shares</td>
                  <td>{t.limitPrice ? `limit ${t.limitPrice.toFixed(2)}` : "market"}</td>
                  <td>{t.stop ? `${t.stop.toFixed(2)} / ${t.target?.toFixed(2)}` : "–"}</td>
                  <td>{fmt.money(t.notional)}</td>
                  <td><TicketActions id={t.id} side={t.side} symbol={t.symbol} qty={t.qty} mode={t.mode} canApprove={canApprove} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Recent orders</h2>
      {past.length === 0 ? (
        <div className="card muted">No orders yet.</div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead><tr><th>When</th><th>Stock</th><th>Order</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {past.map((t) => (
                <tr key={t.id}>
                  <td>{(t.decided ?? t.created).slice(0, 16).replace("T", " ")}</td>
                  <td>{t.symbol}</td>
                  <td>{t.side.toUpperCase()} {t.qty}{t.mode ? ` (${t.mode})` : ""}</td>
                  <td><span className={`badge ${t.status === "SUBMITTED" ? "buy" : t.status === "FAILED" ? "sell" : "open"}`}>{t.status}</span></td>
                  <td className="muted">{t.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted">Entry orders stay open until they fill or you cancel them (<code>python -m app.execution cancel ID</code> or in your broker app). Alpaca trades US stocks only.</p>
    </>
  );
}
