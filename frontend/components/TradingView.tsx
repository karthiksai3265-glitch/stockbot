"use client";
import { useEffect, useRef } from "react";

/** Map our Yahoo-style symbols to TradingView symbols: "BHP.AX" -> "ASX:BHP", "BRK-B" -> "BRK.B". */
export function toTradingView(symbol: string): string {
  if (symbol.endsWith(".AX")) return `ASX:${symbol.slice(0, -3)}`;
  if (symbol.endsWith(".NS")) return `NSE:${symbol.slice(0, -3)}`;
  return symbol.replace("-", ".");
}

function useTVWidget(script: string, config: Record<string, unknown>) {
  const ref = useRef<HTMLDivElement>(null);
  const key = JSON.stringify(config);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.innerHTML = '<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>';
    const s = document.createElement("script");
    s.src = `https://s3.tradingview.com/external-embedding/${script}`;
    s.async = true;
    s.innerHTML = JSON.stringify({ autosize: true, ...config });
    el.appendChild(s);
    return () => { el.innerHTML = ""; };
  }, [script, key]); // eslint-disable-line react-hooks/exhaustive-deps
  return ref;
}

const dark = () => typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;

/** Full interactive TradingView chart (drawing tools, indicators). Free embed widget. */
export function TradingViewChart({ symbol }: { symbol: string }) {
  const ref = useTVWidget("embed-widget-advanced-chart.js", {
    symbol: toTradingView(symbol), interval: "D", timezone: "Australia/Sydney", theme: dark() ? "dark" : "light",
    style: "1", locale: "en", allow_symbol_change: false, hide_side_toolbar: false, support_host: "https://www.tradingview.com",
  });
  return <div ref={ref} className="tradingview-widget-container card" style={{ height: 520, padding: 0, overflow: "hidden" }} />;
}

/** Scrolling index bar shown at the top of every page. Edit the symbols to taste. */
export function TradingViewTape() {
  const ref = useTVWidget("embed-widget-ticker-tape.js", {
    symbols: [
      { proName: "FOREXCOM:SPXUSD", title: "S&P 500" },
      { proName: "FOREXCOM:NSXUSD", title: "Nasdaq 100" },
      { proName: "ASX:XJO", title: "ASX 200" },
      { proName: "NSE:NIFTY", title: "Nifty 50" },
      { proName: "NSE:BANKNIFTY", title: "Bank Nifty" },
    ],
    showSymbolLogo: false, isTransparent: true, displayMode: "adaptive", colorTheme: dark() ? "dark" : "light", locale: "en",
  });
  return <div ref={ref} className="tradingview-widget-container" style={{ height: 46 }} />;
}
