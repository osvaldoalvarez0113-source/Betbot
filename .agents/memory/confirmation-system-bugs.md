---
name: Confirmation system bugs and fixes
description: Known bugs in the ntfy confirmation system and how they were fixed
---

## Bug 1 — Auto-confirm (CRITICAL)
Bot's own ntfy alerts contain the word "aposté" (in "SUGERIDO — responde 'aposté' para registrar").
`_poll_ntfy_confirmations` used `"aposté" in text` (substring match) and read ALL messages from
the last 120 seconds, including bot-published alerts. Result: every pick alert auto-confirmed itself.

**Fix applied:**
- Skip messages where `len(text) > 80` or `"responde" in text` or `"sugerido" in text`
- Command keywords must START the message using `_starts_with_cmd()` — not be embedded
- User commands ("aposté", "cancelé", "pendientes") are always short standalone words

## Bug 2 — Stake logged before user confirms
`queue_for_confirmation` stored the full Kelly stake in the queued entry.
If the bot ever wrote entries to bets_log.csv via any path, the stake was already non-zero.

**Fix applied:**
- `entry["stake"] = 0.0` at queue time
- `entry["suggested_stake"] = stake` stores the Kelly amount
- `_confirm_bet()` restores `entry["stake"] = entry.pop("suggested_stake")` before `log_bets()`

## Rule
`log_bets()` must ONLY be called from `_confirm_bet()`, which is ONLY called from
`_confirm_next_bet()` / `_confirm_all_pending()`, which are ONLY called from
`_poll_ntfy_confirmations()` when user sends "aposté".
