# Channel architecture decision

## Product stance

Pageboy (`pboy`) is an **agent harness orchestrator**, not a human notification hub.

- **ChannelAdapter** = *wake-up* transports that push external stimuli into the Engine  
  (Telegram long-poll, Slack Socket Mode).
- **Ticketing systems (Jira / Confluence)** = *work surfaces* agents reach via **REST**
  from inside the harness loop (GET/POST). They are **not** required as daemon-side
  pollers or public webhook receivers for the core product path.

## Why Jira is agent-pull, not ChannelAdapter

| Approach | Needs public HTTPS? | Fits agent harness? |
|----------|---------------------|---------------------|
| Jira Cloud webhook → local daemon | Yes (or tunnel/relay) | Weak — ops/integration bus |
| Daemon polls Jira REST on a timer | No | Optional later — duplicates agent loop |
| **Agent harness calls Jira REST** | No | **Preferred** — agent owns JQL, cadence, updates |

Webhook “events” from Jira Cloud are push callbacks. There is no Telegram-style
event-queue poll API. The practical no-tunnel alternative is periodic REST search
(`/rest/api/3/search` + JQL watermark), and that loop belongs to the **agent**
(or a thin optional adapter later), not the mandatory inbound spine.

## Consequences for the roadmap

- **M4.1 Slack Socket Mode** — in scope (wake-up channel, no inbound tunnel).
- **M4.2 Jira/Confluence webhook + relay** — **deferred / optional**. Not required
  for agent-first Zero-Ops use.
- **M4.3 Jira outbound sync** — **deferred as a first-class sync target**. Agents
  should comment/transition via Jira REST; Slack thread reply remains a reasonable
  outbound sync later.
- Secrets: store `jira.*` tokens for harness injection when needed; no ChannelAdapter
  required to use them.

## Agent loop sketch

```text
wake (Telegram / Slack / inject)
  → hook exec (agent)
      → Jira REST search / get / transition / comment
      → pboy state put / event complete
  → optional --sync telegram (human-facing reply)
```
