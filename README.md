# Multi-Tenant AI Agent Framework (Telegram + WhatsApp)

One codebase serving many small businesses at once. Each business (tenant) gets its own bot, database, catalog and conversation logic, with no cross-talk.

## What's inside
- `core/` — engine, per-user memory and session manager, Telegram and WhatsApp Cloud API handlers, Google Calendar / Drive / Sheets helpers, email sender, PDF and Excel reports, payment-reference verifier, health monitor.
- `profiles/` — reusable business templates (medical clinic, dental, real estate, car dealer, auto parts, insurance, hardware store, salon, creator community).
- `tenants/` — per-business configuration. Two demo tenants are included; client data is not part of this repo.
- `launcher.py` / `multi_launcher.py` — run one tenant or all of them.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env   # add your keys
python launcher.py --tenant clinica_demo
```

## Notes
Showcase copy: client tenants, payment records and internal sales material were removed. Secrets are read from environment variables only.
