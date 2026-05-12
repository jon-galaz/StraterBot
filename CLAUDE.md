# CLAUDE.md — Strater2 (Semi-Automated Trading System)

Full project specification: [`PROJECT BRIEF md.md`](./PROJECT%20BRIEF%20md.md)

---

## What this project is

Semi-automated trading system. Scanner detects signals → Telegram for manual approval → Alpaca broker API executes approved orders. Trader (partner) approves every trade in v1. Long-only, cash account, 10 US tickers, bracket orders only.

---

## Stack (locked)

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Broker | Alpaca (`alpaca-py`) |
| Data (backtest) | yfinance |
| Data (live) | Alpaca historical + live |
| Indicators | pandas-ta |
| Backtester | backtesting.py (v1) |
| Telegram | python-telegram-bot v21+ (async) |
| Scheduler | APScheduler |
| ORM | SQLAlchemy |
| Config | pydantic-settings + .env |
| Logging | loguru |
| Testing | pytest |
| Packaging | pyproject.toml, uv or poetry |
| Container | Docker + docker-compose |
| DB | SQLite (v1), PostgreSQL (v2) |
| Infra | Single VPS, Hetzner-class |

Do not suggest alternatives unless a locked decision explicitly fails (see `PROJECT BRIEF md.md §3`).

---

## Repository layout (target)

```
trading-system/
├── pyproject.toml
├── CLAUDE.md
├── PROJECT BRIEF md.md
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── src/
│   └── trading_system/
│       ├── config.py
│       ├── rules/            # shared rule engine — single module used by scanner AND backtester
│       │   ├── engine.py
│       │   └── indicators.py
│       ├── data/
│       ├── backtest/
│       ├── scanner/
│       ├── notifier/
│       ├── executor/
│       ├── monitor/
│       ├── store/
│       └── safety/
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

---

## Critical architectural invariant

**The rule engine is a single module (`src/trading_system/rules/`) imported by both scanner and backtester. It must never be duplicated or diverge.** If backtest and scanner use different rule logic, backtest results are lies. Enforce with shared interface + integration tests.

---

## Safety mechanisms (non-negotiable — never remove or bypass)

- Daily max-loss kill-switch: liquidate all + halt new entries.
- Max concurrent positions cap.
- Idempotency keys on every order. No duplicates ever.
- Manual per-trade Telegram approval in v1.
- Heartbeat monitor: missed ping → Telegram alert within 60 s.
- All entries are bracket orders. No naked entries.
- Separate Telegram bot tokens + chat IDs for paper vs live.
- Daily reconciliation: local state vs broker state, alert on mismatch.

---

## Development phases (no skipping)

| Phase | Goal |
|---|---|
| 0 | Specification (rule spec + 10-ticker list) |
| 1 | Backtester end-to-end on historical data |
| 2 | Paper trading 4+ weeks, zero unhandled errors last 7 days |
| 3 | Live micro (50€ positions, kill-switch active) |
| 4 | Live target (200€ positions) |

Currently in **Phase 0 → 1 transition**. See `PROJECT BRIEF md.md §11` for immediate next actions.

---

## Regulatory context

- Spain (EU). Personal-use tool for trader's own account. No CNMV authorization needed.
- Every executed trade is a taxable event. System must produce a complete trade log with timestamps.
- No third-party signal distribution, no managed-account features, no public outputs.

---

## Decision logging (required behavior)

Per `PROJECT BRIEF md.md §13`: every correction, assumption, or decision made during development must be recorded in a `.md` file so that context is preserved across separate Claude Code sessions. Use the memory system at `~/.claude/projects/.../memory/` for session-to-session context and write decision logs as `decisions/YYYY-MM-DD-topic.md` in the repo for permanent record.

---

## Open items (Phase 0 blockers)

- [ ] Alpaca EU account confirmed active?
- [ ] Final 10-ticker list from trader
- [ ] Rule-extraction session scheduled (90 min screen share)
- [ ] Account ownership + tax liability agreement drafted

Update `PROJECT BRIEF md.md §12` as items resolve.
