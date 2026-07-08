# 2026-07-08 — Reconciliation must be order-aware (pending fills ≠ mismatches)

## Symptom
Trader approved two operations; the 16:30 ET daily reconciliation reported
`⚠️ Reconciliation mismatch — LOCAL open: X — not found in Alpaca` for them.

## Root cause
Not state divergence — a false positive baked into the timing:

1. The scanner is scheduled at **16:05 ET**, deliberately after the 16:00 close,
   so it evaluates the completed daily bar. Every approval therefore happens
   while the market is closed.
2. The executor submits a `MarketOrderRequest` + `TimeInForce.DAY` bracket. With
   the market closed, Alpaca **accepts** the order but queues it for the next
   open — no position is created, and `fill_price` stays `NULL` locally.
3. Reconciliation ran at **16:30 ET** and compared local `open` trades against
   `get_all_positions()` only. The queued orders have no position yet, so both
   were flagged as missing.

The position monitor already handled this correctly (it checks pending orders
before ever assuming an order is gone). Reconciliation did not — it was stricter
and dumber, so it cried wolf on **every** post-close approval.

## Decision / fix
Make reconciliation order-aware, mirroring the monitor's "never assume gone"
rule. It now also fetches open orders. A local `open` trade that is **unfilled**
(`fill_price IS NULL`) **and** has a matching accepted/pending Alpaca order is
reported as `⏳ pending fill`, not a mismatch. Still flagged as real drift:
- a **filled** local trade with no Alpaca position (position vanished), and
- an **unfilled** trade with **no** Alpaca order (order truly rejected/expired).

If the open-orders fetch fails, we can't confirm pending state and fall back to
flagging (over-alert rather than hide) — a broker-outage edge case only.

## Verification
`uv run pytest` → 63 passed (added 3 reconciliation tests: pending→ok,
no-order→mismatch, filled-but-missing→mismatch).

## Note for the trader
The earlier two "mismatches" were almost certainly benign: those orders should
have filled at the next market open. Confirm via `/status` / `/signals` or the
Alpaca dashboard the following morning.
