# PolyPulse v2 — Live Polymarket Data API v2 Whale & Smart-Money Radar

> **Note:** Polymarket retired its legacy Data API v1 endpoints with `HTTP 410`. **PolyPulse v2** is built from scratch on **Polymarket Data API v2** (`https://data-api.polymarket.com/v2/trades`, `/v2/leaderboard`, `/v2/positions`).

- **Live Web Terminal:** https://chainguard-studio-production.up.railway.app/poly
- **Live Telegram Alerts Bot:** https://t.me/ChainGuardVaultBot?start=poly

## Features
- **Live $5,000+ Whale Feed:** Streams high-conviction Polymarket fills via `/v2/trades?filter_type=CASH&filter_amount=5000`.
- **Smart-Money Overlap Badges:** Cross-references live whale trades against the **Top-50 PnL Leaderboard** (`/v2/leaderboard`) and flags **Contrarian <35¢** entries.
- **Wallet Position Inspector:** Inspect any Polymarket `0x` proxy wallet's live exposure, average entry, and PnL via `/v2/positions`.
- **Hosted Pro Alerts (Zero-KYC Polygon USDC):** Instant Telegram/Discord push alerts for `$1,000+` fills and up to 25 tracked whale wallets.

## Quick Run (Standalone CLI)
```bash
pip install httpx
python -c "import httpx; print(httpx.get('https://data-api.polymarket.com/v2/trades?limit=5&filter_type=CASH&filter_amount=5000').json())"
```
