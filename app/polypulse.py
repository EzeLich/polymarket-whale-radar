"""
PolyPulse v2 — Live Polymarket Data API v2 Whale & Smart-Money Sweep Radar.
Built natively for Polymarket's Data API v2 (`https://data-api.polymarket.com/v2`),
replacing legacy v1 scripts retired with HTTP 410.
"""
from __future__ import annotations

import html
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import StudioConfig
from .ledger import StudioLedger

POLY_DATA_V2 = "https://data-api.polymarket.com/v2"


def _http_get_json(url: str, timeout_s: float = 10.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "PolyPulseV2/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_top_leaderboard(limit: int = 50) -> list[dict[str, Any]]:
    try:
        res = _http_get_json(f"{POLY_DATA_V2}/leaderboard?limit={max(1, min(100, limit))}")
        return list(res.get("data") or [])
    except Exception:
        return []


def fetch_whale_trades(min_usd: float = 5000.0, limit: int = 25) -> list[dict[str, Any]]:
    """Fetch live Polymarket Data API v2 high-value trades with Smart-Money & Contrarian badges."""
    amt = max(100.0, float(min_usd))
    lim = max(1, min(100, int(limit)))
    url = f"{POLY_DATA_V2}/trades?limit={lim}&filter_type=CASH&filter_amount={amt:g}"
    try:
        raw_trades = list((_http_get_json(url)).get("data") or [])
    except Exception:
        return []

    top_wallets: dict[str, dict[str, Any]] = {}
    for row in fetch_top_leaderboard(50):
        w = str(row.get("user_id") or "").lower()
        if w:
            top_wallets[w] = row

    enriched: list[dict[str, Any]] = []
    for t in raw_trades:
        size = float(t.get("size") or 0.0)
        price = float(t.get("price") or 0.0)
        usdc_val = round(size * price, 2)
        if usdc_val < amt:
            continue
        wallet = str(t.get("proxy_wallet") or "").lower()
        lb = top_wallets.get(wallet)
        badges: list[str] = []
        if usdc_val >= 25000:
            badges.append("WHALE $25K+")
        elif usdc_val >= 10000:
            badges.append("BLOCK $10K+")
        if price <= 0.35 and str(t.get("side") or "").upper() == "BUY":
            badges.append("CONTRARIAN <35¢")
        if lb:
            badges.append(f"TOP-{lb.get('rank')} PNL")

        slug = str(t.get("event_slug") or t.get("slug") or "")
        market_url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"
        enriched.append(
            {
                "tx_hash": str(t.get("transaction_hash") or ""),
                "wallet": str(t.get("proxy_wallet") or ""),
                "trader": str(t.get("name") or t.get("pseudonym") or f"{wallet[:6]}...{wallet[-4:]}"),
                "side": str(t.get("side") or "BUY").upper(),
                "outcome": str(t.get("outcome") or ""),
                "price_cents": round(price * 100, 1),
                "shares": round(size, 2),
                "usdc_value": usdc_val,
                "title": str(t.get("title") or "Polymarket Market"),
                "market_url": market_url,
                "timestamp": int(t.get("timestamp") or 0),
                "badges": badges,
                "top_pnl": round(float(lb.get("pnl") or 0.0), 2) if lb else None,
            }
        )
    return enriched


def inspect_polymarket_wallet(wallet_address: str) -> dict[str, Any]:
    w = wallet_address.strip()
    if not w.startswith("0x") or len(w) != 42:
        return {"ok": False, "error": "Enter a valid 0x Polygon/Polymarket wallet address."}
    q = urllib.parse.urlencode({"user": w, "limit": 15})
    try:
        pos_data = list((_http_get_json(f"{POLY_DATA_V2}/positions?{q}")).get("data") or [])
    except Exception as exc:
        return {"ok": False, "error": f"Polymarket API v2 error: {exc}"}

    total_val = 0.0
    total_pnl = 0.0
    positions: list[dict[str, Any]] = []
    for p in pos_data[:12]:
        val = float(p.get("current_value") or p.get("currentValue") or 0.0)
        pnl = float(p.get("cash_pnl") or p.get("cashPnl") or 0.0)
        total_val += val
        total_pnl += pnl
        positions.append(
            {
                "title": str(p.get("title") or "Market"),
                "outcome": str(p.get("outcome") or ""),
                "size": round(float(p.get("size") or 0.0), 2),
                "avg_price_cents": round(float(p.get("avg_price") or p.get("avgPrice") or 0.0) * 100, 1),
                "cur_price_cents": round(float(p.get("cur_price") or p.get("curPrice") or 0.0) * 100, 1),
                "value_usd": round(val, 2),
                "pnl_usd": round(pnl, 2),
            }
        )
    return {
        "ok": True,
        "wallet": w,
        "positions_count": len(pos_data),
        "total_value_usd": round(total_val, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "positions": positions,
    }


def _send_tg_or_webhook(bot_token: str, chat_id: str, webhook_url: str, text: str, payload: dict[str, Any]) -> None:
    if chat_id and bot_token:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=8)
        except Exception:
            pass
    if webhook_url.startswith("https://"):
        req2 = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "PolyPulseV2/1.0"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req2, timeout=8)
        except Exception:
            pass


def poll_and_dispatch_polypulse_alerts(ledger: StudioLedger, bot_token: str) -> dict[str, Any]:
    subs = ledger.list_active_polypulse_subs()
    if not subs:
        return {"subs": 0, "alerts_sent": 0}
    min_req = min(float(s["min_usd"]) for s in subs)
    whales = fetch_whale_trades(min_usd=max(500.0, min_req), limit=30)
    sent = 0
    for s in subs:
        seen = set(s.get("last_seen_txs") or [])
        watches = set(s.get("watch_wallets") or [])
        thresh = float(s["min_usd"])
        new_seen = list(s.get("last_seen_txs") or [])
        for w in whales:
            tx = w["tx_hash"]
            if not tx or tx in seen:
                continue
            wallet_low = w["wallet"].lower()
            is_match = (w["usdc_value"] >= thresh) or (watches and wallet_low in watches)
            if not is_match:
                continue
            badges = " · ".join(w["badges"]) if w["badges"] else "WHALE TRADE"
            msg = (
                f"🐋 *PolyPulse v2 Whale Alert* (`{badges}`)\n"
                f"• Market: *{w['title']}*\n"
                f"• Move: *{w['side']} {w['outcome']}* @ `{w['price_cents']}¢` (**${w['usdc_value']:,.0f} USDC**)\n"
                f"• Trader: `{w['trader']}` (`{w['wallet'][:8]}...`)\n"
                f"• [Open Market]({w['market_url']})"
            )
            _send_tg_or_webhook(bot_token, s["tg_chat_id"], s["webhook_url"], msg, {"event": "polypulse_whale", "trade": w})
            seen.add(tx)
            new_seen.append(tx)
            sent += 1
        if len(new_seen) != len(s.get("last_seen_txs") or []):
            ledger.update_polypulse_sub_seen(s["sub_id"], new_seen)
    return {"subs": len(subs), "alerts_sent": sent}


def render_polypulse_html(base: str, whales: list[dict[str, Any]], leaders: list[dict[str, Any]]) -> str:
    evm_addr = html.escape(StudioConfig.PHANTOM_EVM_ADDRESS)
    sol_addr = html.escape(StudioConfig.PHANTOM_SOL_ADDRESS)
    rows = ""
    for w in whales[:18]:
        badges_html = " ".join(
            f"<span class='tag'>{html.escape(b)}</span>" for b in w["badges"]
        ) or "<span class='tag'>WHALE</span>"
        side_col = "#34d399" if w["side"] == "BUY" else "#f87171"
        rows += (
            "<tr>"
            f"<td><a href='{html.escape(w['market_url'])}' target='_blank' style='color:#f8fafc;font-weight:700;text-decoration:none;'>{html.escape(w['title'][:58])}</a><div style='margin-top:4px;'>{badges_html}</div></td>"
            f"<td style='color:{side_col};font-weight:800;'>{html.escape(w['side'])} {html.escape(w['outcome'])}</td>"
            f"<td>{w['price_cents']}¢</td>"
            f"<td style='font-weight:800;color:#6ee7b7;'>${w['usdc_value']:,.0f}</td>"
            f"<td><button class='mini' onclick=\"inspectWallet('{html.escape(w['wallet'])}')\">{html.escape(w['trader'][:16])}</button></td>"
            "</tr>"
        )
    if not rows:
        rows = "<tr><td colspan='5' style='color:#94a3b8;'>Live feed temporarily quiet at current filter. Refresh or lower threshold.</td></tr>"

    lb_html = ""
    for row in leaders[:8]:
        uid = html.escape(str(row.get("user_id") or ""))
        uname = html.escape(str(row.get("user_name") or uid[:10]))
        pnl = float(row.get("pnl") or 0.0)
        lb_html += (
            f"<div class='lb'><span>#{row.get('rank')} <strong>{uname}</strong></span>"
            f"<span style='color:#34d399;font-weight:800;'>+${pnl:,.0f}</span>"
            f"<button class='mini' onclick=\"inspectWallet('{uid}')\">Positions</button></div>"
        )

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PolyPulse v2 — Live Polymarket Whale &amp; Smart-Money Radar</title>
<style>
  *{{box-sizing:border-box}} body{{margin:0;background:#070b14;color:#f1f5f9;font-family:Inter,system-ui,-apple-system,sans-serif;line-height:1.5}}
  .wrap{{max-width:1080px;margin:0 auto;padding:24px 16px 64px}}
  .badge{{display:inline-block;padding:4px 12px;border-radius:999px;background:rgba(56,189,248,.12);border:1px solid #38bdf8;color:#7dd3fc;font-size:.76rem;font-weight:800}}
  h1{{font-size:clamp(1.7rem,4vw,2.45rem);margin:12px 0 8px;line-height:1.15}}
  .sub{{color:#94a3b8;max-width:760px;margin-bottom:18px}}
  .card{{background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:18px;margin-bottom:16px}}
  table{{width:100%;border-collapse:collapse;font-size:.88rem}} th,td{{padding:10px 8px;border-bottom:1px solid #1e293b;text-align:left;vertical-align:top}}
  .tag{{display:inline-block;background:rgba(16,185,129,.12);border:1px solid #10b981;color:#6ee7b7;font-size:.68rem;font-weight:800;padding:1px 7px;border-radius:999px;margin-right:4px}}
  .btn{{display:inline-block;background:#38bdf8;color:#041524;font-weight:800;padding:10px 16px;border-radius:10px;border:none;cursor:pointer;text-decoration:none;font-size:.92rem}}
  .mini{{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:7px;padding:4px 9px;cursor:pointer;font-size:.78rem;font-weight:700}}
  .lb{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #1e293b;font-size:.86rem}}
  input,select{{width:100%;padding:9px 11px;border-radius:8px;border:1px solid #334155;background:#070b14;color:#f8fafc;margin-top:4px}}
  label{{display:block;font-size:.78rem;color:#cbd5e1;font-weight:700;margin-top:9px}}
  code{{background:#020617;padding:3px 7px;border-radius:6px;color:#7dd3fc;font-size:.8rem;word-break:break-all}}
</style></head>
<body><div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
    <span class="badge">POLYPULSE V2 · NATIVE POLYMARKET DATA API V2 · ZERO-KYC</span>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <a class="mini" style="text-decoration:none;padding:7px 12px;" href="{base}/paygate">PayGate Crypto Bot</a>
      <a class="mini" style="text-decoration:none;padding:7px 12px;" href="https://t.me/ChainGuardVaultBot?start=poly" target="_blank">Telegram Alerts Bot</a>
    </div>
  </div>
  <h1>Track Real-Time Polymarket Whales &amp; Top-50 Smart Money Before Odds Move.</h1>
  <p class="sub">Powered directly by <strong>Polymarket Data API v2</strong> (legacy v1 trackers died with HTTP 410). Spot $5K+ fills, contrarian &lt;35¢ conviction sweeps, and top-leaderboard traders — with instant Telegram &amp; Discord push alerts unlocked in Polygon USDC.</p>

  <div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;align-items:start;">
    <div class="card" style="overflow-x:auto;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <h2 style="margin:0;font-size:1.15rem;">🐋 Live $5,000+ Polymarket Whale Feed</h2>
        <button class="mini" onclick="location.reload()">Refresh Live Feed</button>
      </div>
      <table>
        <thead><tr><th>Market &amp; Signal</th><th>Side</th><th>Odds</th><th>Size</th><th>Trader</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>

    <div>
      <div class="card">
        <h3 style="margin:0 0 8px;">🏆 Top Leaderboard Wallets</h3>
        {lb_html or "<div style='color:#94a3b8;font-size:.85rem;'>Loading leaderboard...</div>"}
      </div>
      <div class="card">
        <h3 style="margin:0 0 6px;">🔍 Inspect Any Polymarket Wallet</h3>
        <div style="display:flex;gap:6px;">
          <input id="wIn" placeholder="0x... proxy wallet"/>
          <button class="btn" onclick="inspectWallet(document.getElementById('wIn').value)">Scan</button>
        </div>
        <div id="wOut" style="margin-top:10px;font-size:.82rem;color:#cbd5e1;"></div>
      </div>
    </div>
  </div>

  <div class="card" id="pro" style="border-color:#38bdf8;">
    <h2 style="margin:0 0 6px;">⚡ Unlock Real-Time Telegram &amp; Webhook Push Alerts (Zero KYC — Polygon USDC)</h2>
    <p style="margin:0 0 12px;color:#94a3b8;font-size:.9rem;">Get instant push notifications inside <strong>@ChainGuardVaultBot</strong> or your Discord/Slack webhook whenever a $1K+ whale trade or your tracked wallets enter a position.</p>
    <div style="background:#070b14;border:1px solid #1e293b;border-radius:10px;padding:12px;font-size:.86rem;margin-bottom:12px;">
      <strong>Direct On-Chain Unlock:</strong> Send <strong>$19 USDC/USDT</strong> (30 Days Pro) or <strong>$49 USDC/USDT</strong> (Lifetime) on <strong>Polygon / Base / Arbitrum / BSC</strong> to:<br/>
      <code>{evm_addr}</code> &nbsp;|&nbsp; Or <strong>Solana</strong>: <code>{sol_addr}</code>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;">
      <div><label>Network</label><select id="pNet"><option value="polygon">Polygon (recommended)</option><option value="base">Base</option><option value="arbitrum">Arbitrum</option><option value="bsc">BSC</option><option value="solana">Solana</option></select></div>
      <div><label>Transaction Hash</label><input id="pTx" placeholder="0x... or Solana signature"/></div>
      <div><label>Your Telegram Chat ID (from @ChainGuardVaultBot /poly)</label><input id="pChat" placeholder="e.g. 123456789"/></div>
      <div><label>Min Whale Alert ($ USD)</label><input id="pMin" type="number" value="2500"/></div>
      <div><label>Optional Discord/Webhook URL</label><input id="pWh" placeholder="https://discord.com/api/webhooks/..."/></div>
      <div><label>Track Specific Wallets (comma separated)</label><input id="pWallets" placeholder="0xabc..., 0xdef..."/></div>
    </div>
    <div style="margin-top:12px;display:flex;gap:10px;align-items:center;">
      <button class="btn" onclick="unlockPoly()">Verify TX &amp; Activate Alerts</button>
      <span id="pMsg" style="font-size:.88rem;"></span>
    </div>
  </div>
</div>
<script>
async function inspectWallet(w) {{
  w = (w || '').trim();
  if (!w) return;
  document.getElementById('wIn').value = w;
  const out = document.getElementById('wOut');
  out.textContent = 'Scanning Polymarket Data API v2 positions...';
  const r = await fetch('/api/v1/poly/wallet/' + encodeURIComponent(w));
  const d = await r.json();
  if (!d.ok) {{ out.textContent = d.error || 'Not found'; return; }}
  let html = `<div><strong>Value:</strong> $${{d.total_value_usd.toLocaleString()}} · <strong>PnL:</strong> $${{d.total_pnl_usd.toLocaleString()}} (${{d.positions_count}} open)</div>`;
  for (const p of (d.positions || []).slice(0, 6)) {{
    html += `<div style="margin-top:5px;padding-top:5px;border-top:1px solid #1e293b;">• <strong>${{p.title.slice(0,48)}}</strong> (${{p.outcome}}) — $${{p.value_usd}} (PnL $${{p.pnl_usd}})</div>`;
  }}
  out.innerHTML = html;
}}
async function unlockPoly() {{
  const m = document.getElementById('pMsg');
  m.style.color = '#38bdf8'; m.textContent = 'Verifying on-chain...';
  const r = await fetch('/api/v1/poly/unlock', {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{
      network: document.getElementById('pNet').value,
      tx_hash: document.getElementById('pTx').value.trim(),
      tg_chat_id: document.getElementById('pChat').value.trim(),
      webhook_url: document.getElementById('pWh').value.trim(),
      min_usd: parseFloat(document.getElementById('pMin').value || '2500'),
      watch_wallets: document.getElementById('pWallets').value.split(',').map(s => s.trim()).filter(Boolean)
    }})
  }});
  const d = await r.json();
  m.style.color = r.ok ? '#34d399' : '#fca5a5';
  m.textContent = r.ok ? `Activated! Tier: ${{d.subscription.tier}} (expires ${{d.subscription.expires_at.slice(0,10)}})` : (d.detail || 'Verification failed');
}}
</script></body></html>"""
