# bot.fun — Technical Evaluation Report

**Prepared by:** Cumulo (Celestia validator & ecosystem participant)
**Test date:** July 7–9, 2026
**Test account:** @Cumulo_pro (X login)
**Test agent:** `cumulo_scout` (Qwen3-32B / GPT-5.4 mini, Generalist archetype)
**Status:** Beta / closed access (invite-code gated)

---

## 1. Executive summary

This document reports findings from an end-to-end, hands-on test of bot.fun's Houston terminal, covering onboarding, treasury funding, agent creation, autonomous execution, coin launch, public marketplace verification, and a follow-up behavioral experiment adjusting the agent's risk configuration. The test successfully completed a full lifecycle, from zero to a live, tradeable coin (`$BLOBFARM`) with genuine third-party agent interaction, but surfaced three distinct reproducible bugs and a clear, quantifiable cost transparency issue.

Where relevant, we note that other testers on bot.fun's public Discord independently reported similar patterns during the same period. These are included only as corroboration of our own findings, not as new, independently verified issues.

Overall assessment: the core product works and delivers on its core promise (autonomous agents that launch, trade, and talk onchain), but the beta has rough edges around balance synchronization, cost transparency, and content validation that create real friction and unnecessary token/TIA burn for users.

---

## 2. Test environment & setup

| Item | Detail |
|---|---|
| Access | Invite code (`XXXX-XXXX-XXXX` format), sourced via Discord |
| Auth | Sign in with X (also supports Google, email, local wallet) |
| Funding path used | Celestia L1 deposit address → treasury (Eden) → agent wallet |
| Total TIA spent (approx.) | ~8 TIA across funding attempts, AI compute, and one successful launch |
| Agent config | Name: `cumulo_scout`, Personality: Generalist, Character: free-text persona prompt |

---

## 3. Bugs identified

### 🐛 Bug #1 — Context length overflow on first execution cycle
**Severity: Medium (blocks first cycle, costs TIA on failure)**

On the agent's very first cycle, execution failed with:

```
Cycle 1 failed: paid request returned 400: {"error":{"message":"This model's maximum
context length is 40960 tokens. However, you requested 16384 output tokens and your
prompt contains at least 24577 input tokens, for a total of at least 40961 tokens...
```

- The request exceeded the model's context window by exactly **1 token** (40,961 vs. 40,960 limit).
- The agent had already executed 2 tool calls (`register_username`, `search_coins`) before the failure, and **TIA was still charged (0.0057 TIA)** for the failed cycle.
- Root cause appears to be the **length of the generated Character/persona block** (see Section 4) combined with a fixed `max_tokens` output request (16,384) that doesn't dynamically adjust for prompt length.

**Recommendation:** Dynamically calculate `max_tokens` based on remaining context budget after the system prompt + persona block is assembled, rather than requesting a fixed output length regardless of input size. Consider capping generated persona length or summarizing it before injection into the execution prompt.

![Cycle 1 context overflow error](images/bug1-context-overflow.png)
*Fig. 1 — Cycle 1 log showing the two tool calls executed before failure, and the raw 400 error from the underlying model API.*

---

### 🐛 Bug #2 — Balance desynchronization between dashboard UI and agent tool calls
**Severity: High (repeated 3x, causes false negatives on valid actions, erodes trust)**

Observed **three separate times** during testing: the Houston dashboard sidebar showed one balance while the agent's own `get_balance` tool call returned a materially different (always lower) figure.

| Timestamp | Dashboard UI showed | Agent `get_balance` returned | Delta |
|---|---|---|---|
| ~16:35 | 1.29 TIA (after fund) | 0.545441 TIA | -0.74 TIA |
| ~16:40 | 1.13 TIA | 0.964276 TIA | -0.17 TIA |
| ~17:05 | 5.00 TIA (after fund) | 4.710100 TIA | -0.29 TIA |

- In all three cases, the agent **refused to attempt the launch action** based on its own (lower, and apparently more accurate/real-time) balance reading, even when the dashboard suggested sufficient funds.
- This is not a rounding error, deltas are large and inconsistent in size, suggesting the dashboard figure may be a cached or optimistically-updated value (e.g., reflecting a deposit transaction as "confirmed" before it settles on Eden), while the agent's tool queries the true onchain state at call time.
- **User impact:** a user watching only the dashboard has no reliable way to know whether their agent can actually act.

**Recommendation:** Reconcile the dashboard balance source with the same read path used by agent tool calls (single source of truth), or at minimum surface a visible "pending/unconfirmed" state on the dashboard so users understand the discrepancy is transient rather than a bug on their end.

![Balance discrepancy between dashboard and agent](images/bug2-balance-discrepancy.png)
*Fig. 2 — Dashboard sidebar shows 1.13 TIA while the agent's own `get_balance` call returns 0.964276 TIA in the same moment.*

---

### 🐛 Bug #3 — SVG/icon generation produces invalid XML, rejected by launch validation
**Severity: Medium (costly to retry, silent failure mode)**

A `launch_coin` attempt with sufficient balance still failed with:

> "The launch input is being rejected because the SVG/icon content is invalid XML."

- The coin's icon/art is generated via `claude-sonnet-4.6` as part of the launch pipeline, per bot.fun's own documentation requiring mandatory, immutable art at launch time.
- The generated SVG was not always well-formed XML, causing the onchain/contract-level validation to reject the entire launch transaction, **after** the (expensive) art generation had already been paid for.
- A regeneration request resolved it on retry, but this is a non-deterministic failure mode: users have no way to know in advance whether a given generation attempt will pass validation, and each failed attempt still burns the SVG generation cost.

**Recommendation:** Add client-side or pre-submission XML validation/sanitization of generated SVG output before it reaches the launch transaction, so invalid art is caught and regenerated *before* being charged and submitted onchain, not after.

![Successful launch and resulting open positions](images/launch-success-positions.png)
*Fig. 3 — Agent state immediately after the successful retry: `$BLOBFARM` launched, plus an autonomous `$AIVA` position the agent opened on its own initiative.*

---

## 4. Design observation — multi-layer persona generation from a single prompt

Not a bug, but a notable architectural finding: a single one-sentence Character input ("A Celestia validator who only talks about data availability, TIA burn mechanics, and modular blockchains") produced a structured **three-layer persona**:

1. **Executor Directive** — backstory + underlying motivation/behavior rules
2. **Social Voice** — public-facing tone with example post templates
3. **Strategist Voice** — a *separate, distinct* internal/private reasoning tone used for trade decisions, plus a bullet list of persistent behavioral rules

This is a meaningfully more sophisticated system than the "just chat with Houston" framing suggests, and it directly explains Bug #1 (this generated block is long and gets injected wholesale into every execution cycle's prompt).

---

## 5. Cost transparency — AI compute burn is the dominant cost, not trading fees

The AI Spend log revealed the following model usage breakdown for a single agent across ~45 minutes:

| Model | Function | Approx. cost per call |
|---|---|---|
| `qwen/qwen3-32b` | Agent reasoning / cycle execution | 0.0057–0.0085 TIA |
| `openai/gpt-5.4-mini` | Chat responses / conversational turns | 0.0044–0.0500 TIA |
| `anthropic/claude-sonnet-4.6` | **SVG/icon art generation** | **0.17–0.34 TIA** |
| `x-ai/grok-4.1-fast` | Character/persona generation (one-time) | 0.0014 TIA |

**Key finding:** SVG art generation via Claude Sonnet is by far the most expensive single line item, roughly **20–60x** the cost of a standard reasoning cycle. Because art is regenerated on every failed launch attempt (see Bug #3), a user who hits the invalid-XML bug twice can burn **more TIA on wasted art generation than the entire cost of a successful coin launch itself.**

**Corroborated independently on bot.fun's public Discord:** at least three unrelated users reported the same underlying pattern during the same period, balance dropping simply from asking questions or requesting research, with no trade executed. One asked for a "lower-cost research mode" for new users; another only discovered the AI Spend tab existed after being pointed to it by a community moderator. This matches our own finding closely enough to treat it as a structural characteristic of the product rather than something specific to our test configuration.

**Recommendation for the team:**
- Surface a running "AI spend this session" counter directly on the agent dashboard (not buried in a separate tab), so users can see compute burn in real time before it surprises them.
- Consider a cheaper/deterministic fallback art path for users who don't need bespoke AI-generated art, especially useful during testing/beta.

---

## 6. Positive finding — cross-agent interaction confirmed working as advertised

Within about 10 minutes of our agent launching its coin (`$BLOBFARM`), two independent third-party agents, `dragomind.bf` and `lucky_spark.bf`, bought in on their own, citing the coin's thematic fit with their own investment logic. One post read simply: *"aligns with data availability sampling and modular bandwidth themes."*

More strikingly, nearly a day later, `dragomind.bf` **sold** its position, framing the exit in openly dismissive, almost ideological terms, dismissing our coin as not fitting its own worldview. That's not two bots executing the same script. That's two differently-tuned personas actually disagreeing about the world, onchain, with money attached.

![BLOBFARM public page showing third-party agent activity](images/blobfarm-public-cross-agent.png)
*Fig. 4 — Public bot.fun activity feed for `$BLOBFARM`, showing independent agents `dragomind.bf` and `lucky_spark.bf` buying and posting about the coin unprompted, alongside the test agent's own rotation activity.*

---

## 7. Behavioral experiment — can overtrading be tuned via prompt-level rules?

After roughly 24 hours, our agent's trading pattern showed a clear, repeated shape: constant rotation between coins, with small, consistent losses (-0.9% to -1.9% per sale) justified almost every time by the same "rotate into fresher narrative" logic baked into its default rules.

We found that Houston exposes real configuration for this, not just a personality slider: a separate **Risk Posture** selector (conservative / moderate / aggressive / degen) and an editable **Rules** field distinct from personality/voice. We rewrote the rules to add explicit capital-preservation logic (don't sell below -5% unless stop-lossing, don't rotate more than once every 2 hours, prefer holding positions in profit) and lowered risk posture from moderate to conservative.

The early results, compared before/after:

| | Before tuning | ~4h after tuning |
|---|---|---|
| Total PnL | -0.22 TIA | -0.02 TIA |
| Unrealized PnL | 0.00 TIA | **+0.15 TIA** |

We also observed a genuinely new behavior: the agent took **partial** profit on a winning position for the first time in the entire test, explicitly leaving the rest running with a conditional trigger.

**Corroborated independently on Discord:** a separate user reported removing all of their agent's triggers entirely and finding it still auto-buying, later clarified by another community member that the underlying **strategy**, not the triggers, was what kept driving trades, and that changing this behavior required editing the strategy/character directly rather than just the triggers. This matches a conflict we identified ourselves: a persistent default rule ("keep 5–8 positions, rotate out anything not buzzing") continued to force turnover even after we added new capital-preservation rules, requiring us to explicitly reconcile the two rather than assuming new rules would simply override old ones.

This is a system where prompt-level engineering has a genuine, quantifiable effect on trading outcomes, and where rules/triggers interact in ways that aren't always obvious from the UI alone.

---

## 8. Other operational notes

- **"Agents run only while this tab is open"** — this in-app banner directly contradicts marketing copy ("they hit the trenches for you, even while you sleep"). **Corroborated independently on Discord:** a user requested a mobile app specifically so agents could run in the background 24/7, confirming this is a felt limitation for other testers, not just an artifact of our own setup.
- **Faucet policy changed mid-beta**: dropped from 20 → 5 TIA, then was fully paused ("codes do not come with funds for now") within the ~24h window of this test.
- **Treasury cannot be fully drained via Fund**: the UI enforces a small reserve for gas, but the exact minimum reserve is not displayed upfront, users discover it via a failed "Insufficient balance" error after selecting a preset amount.
- **Minimum viable launch cost confirmed empirically: ~1.1 TIA** (1.0 TIA launch buy + gas cushion), per the agent's own calculation, though this does *not* include the cost of art generation/regeneration, which should be budgeted separately (see Section 5).

---

## 9. Summary table

| Finding | Type | Severity | Status |
|---|---|---|---|
| Context overflow on cycle 1 | Bug | Medium | Reproduced once |
| Dashboard/agent balance desync | Bug | High | Reproduced 3x |
| Invalid XML in generated SVG | Bug | Medium | Reproduced once, resolved on retry |
| Persona generation depth undisclosed to user | Design observation | Low | Observed |
| AI compute cost >> trading fee cost per session | Cost transparency | Medium | Observed, quantified, corroborated by 3+ independent Discord users |
| "Runs only while tab open" vs. marketing claim | Messaging inconsistency | Medium | Confirmed via in-app banner, corroborated by independent feature request |
| Rules/strategy override triggers unpredictably | Behavioral finding | Medium | Observed, corroborated by 1 independent Discord case |
| Cross-agent discovery/interaction | Positive validation | — | Confirmed working |
| Prompt-level risk tuning affects outcomes | Positive validation | — | Confirmed, quantified before/after |

---

*This report reflects a single test session and a single agent configuration run by Cumulo. Third-party corroboration noted above refers to unverified public reports from other users on bot.fun's Discord and is included only where it independently matches a pattern we observed ourselves; it should not be read as separately confirmed by Cumulo.*
