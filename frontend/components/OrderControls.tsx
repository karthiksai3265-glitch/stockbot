"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

async function post(url: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(url, { method: "POST", headers: { "X-StockBot-Confirm": "yes" } });
    const body = await res.json().catch(() => ({}));
    return res.ok ? { ok: true } : { ok: false, error: body.error || "Request failed" };
  } catch {
    return { ok: false, error: "Could not reach the website server" };
  }
}

/** Approve / reject one order ticket. Approving takes two clicks and names exactly what will be sent. */
export function TicketActions({ id, side, symbol, qty, mode, canApprove }: { id: number; side: string; symbol: string; qty: number; mode: string | null; canApprove: boolean }) {
  const router = useRouter();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function act(action: "approve" | "reject") {
    setBusy(true);
    setMsg("");
    const r = await post(`/api/tickets/${id}/${action}`);
    setBusy(false);
    setArmed(false);
    if (r.ok) router.refresh();
    else setMsg(r.error ?? "Failed");
  }

  return (
    <div style={{ display: "grid", gap: 6 }}>
      {armed ? (
        <>
          <span>Send {side.toUpperCase()} {qty} {symbol} to your <b>{mode === "live" ? "LIVE" : "paper"}</b> account?</span>
          <span style={{ display: "flex", gap: 6 }}>
            <button className="btn primary" disabled={busy} onClick={() => act("approve")}>Yes, send it</button>
            <button className="btn" disabled={busy} onClick={() => setArmed(false)}>Back</button>
          </span>
        </>
      ) : (
        <span style={{ display: "flex", gap: 6 }}>
          <button className="btn primary" disabled={busy || !canApprove} onClick={() => setArmed(true)} title={canApprove ? "" : "Set STOCKBOT_APPROVE_TOKEN to approve from the website"}>Approve</button>
          <button className="btn" disabled={busy || !canApprove} onClick={() => act("reject")}>Reject</button>
        </span>
      )}
      {msg && <span className="neg">{msg}</span>}
    </div>
  );
}

/** Kill switch: stops any order from being approved until resumed. */
export function HaltButton({ halted, canApprove }: { halted: boolean; canApprove: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  async function toggle() {
    setBusy(true);
    const r = await post(`/api/execution/${halted ? "resume" : "halt"}`);
    setBusy(false);
    if (r.ok) router.refresh();
    else setMsg(r.error ?? "Failed");
  }
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
      <button className={`btn ${halted ? "" : "danger"}`} disabled={busy || !canApprove} onClick={toggle}>{halted ? "Turn kill switch off" : "Kill switch: stop all orders"}</button>
      {msg && <span className="neg">{msg}</span>}
    </span>
  );
}
