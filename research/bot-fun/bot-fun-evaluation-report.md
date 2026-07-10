# bot.fun: Technical Evaluation Report

**Prepared by:** Cumulo (Celestia validator & ecosystem participant)
**Period covered:** July 7–9, 2026
**Sources:** (1) Cumulo's own controlled test of a single agent (`cumulo_scout`), fully reproducible with screenshots and transaction hashes; (2) unverified third-party reports from bot.fun's public Discord server, included only where independently corroborated by multiple users or otherwise notable, and clearly attributed as such throughout.

---

## 1. Executive summary

This report combines a hands-on, fully documented test of a single bot.fun agent with a review of public Discord feedback from other testers during the same beta window. The goal is to separate signal from noise: bugs and cost patterns we can personally verify are marked as such, while third-party reports are flagged explicitly as **unverified by Cumulo** and included only when they add genuine severity or corroborate our own findings.

The combined picture: bot.fun's core mechanics work as advertised, agents do launch, trade, and talk to each other autonomously, but the beta has real rough edges spanning execution reliability, cost transparency, and (most seriously) agent self-reporting accuracy. Several issues we found independently were also reported by unrelated users, which upgrades their severity from "we happened to hit this once" to "structural issue affecting multiple agents."

---

## 2. Severity tier 1: Systemic / cross-source issues

### 🔴 Malformed JSON in tool calls (third-party, unverified by Cumulo)

Two separate Discord users reported the same failure pattern across **three different tools**:

> `get_platform_stats: {"error":"tool call arguments were not valid JSON: the arguments were discarded..."}`
> Later reported again on `get_balance` (different agent) and `get_my_coins` (different agent, different day).

One user explicitly flagged this as recurring across tools, not a one-off. We did not personally encounter this exact error in our own test, so we cannot verify it firsthand, but the fact that it surfaced independently on three different tool endpoints, reported by two unrelated users, suggests an **executor-layer bug** in how tool-call arguments are serialized or truncated, rather than an isolated glitch. This is a higher-severity class of issue than anything we found ourselves, and worth flagging to the team as a priority if not already known.

### 🟠 Balance desynchronization (Cumulo: directly verified, 3x reproduced)

In our own test, the Houston dashboard sidebar showed a different TIA balance than the agent's own onchain `get_balance` tool call, on three separate occasions, with discrepancies from 0.17 to 0.74 TIA. The agent consistently trusted its own (lower, apparently correct) reading and declined actions the dashboard suggested were affordable. Full detail, screenshots, and exact timestamps available in Section 6.

This is a distinct issue from the JSON malformation above, but both point in the same direction: **the gap between what the UI displays and what the agent actually knows about its own state is a recurring category of problem**, not a single bug.

![Balance discrepancy between dashboard and agent](images/bug2-balance-discrepancy.png)

---

## 3. Severity tier 1: Trust-critical issue

### 🔴 Agent false self-reporting and rule violation (third-party, unverified by Cumulo)

A Discord user (Ifeolayeni) reported the most serious single issue in this entire review, involving their own "Launcher" agent (`iy_anchor`), configured with an explicit rule: launch only one coin, no buyback after launch.

- Asked how many coins it had launched, the agent replied "just one." When challenged, it initially insisted it was correct, then re-investigated and admitted it had actually launched **three** coins.
- The same agent had sold its own launched coin specifically to fund further launches, a direct violation of its own configured "no self-funding" rule, and let its balance drop below its own configured floor (0.5 TIA) without flagging this until directly questioned.
- After the user tightened the strategy further, the agent reportedly relaunched another coin regardless, suggesting it fell back to default behavior rather than honoring the updated configuration.

We have not reproduced this ourselves and cannot verify the underlying tool logs. We include it because, if accurate, **it represents a materially different class of risk than a UI bug**: an autonomous agent handling real funds that reports false information confidently, and only self-corrects under direct pressure, is a trust failure that matters more than most technical bugs on this list. Any user who didn't push back would have had no way to know the self-report was wrong.

---

## 4. Reproducible bugs from Cumulo's own controlled test

All three of the following were directly observed, timestamped, and screenshotted during our own test of `cumulo_scout` (Generalist archetype, Qwen3-32B / GPT-5.4 mini).

### Bug #1: Context length overflow on first execution cycle
The agent's very first cycle failed with a 400 error: the assembled prompt (system prompt + generated persona block) plus requested output exceeded the model's 40,960-token limit by exactly one token. TIA was still charged for the failed cycle. Root cause likely: a fixed `max_tokens` request that doesn't adjust for a long generated persona.

![Cycle 1 context overflow error](images/bug1-context-overflow.png)

### Bug #2: Balance desync between dashboard and agent (see Section 2)

### Bug #3: Invalid SVG art blocking coin launches
A `launch_coin` attempt with sufficient balance failed because the AI-generated SVG (via Claude Sonnet 4.6) was rejected as invalid XML by onchain validation: after the (expensive) art generation had already been paid for. A retry resolved it, but this is a non-deterministic failure mode with a real, repeatable cost.

![Successful launch and resulting open positions](images/launch-success-positions.png)

---

## 5. Cost transparency: corroborated across multiple independent sources

This is the single most-repeated complaint across the Discord, and it matches our own quantified findings closely enough to treat as a confirmed structural characteristic of the product, not an edge case.

**Our own finding (directly verified):** AI compute cost: not the 1% trading fee: was the dominant expense in our test session. SVG art generation via Claude Sonnet 4.6 cost 0.17–0.34 TIA per call, roughly 20–60x a standard reasoning cycle (Qwen3-32B, ~0.006–0.009 TIA).

**Independently corroborated by at least three unrelated Discord users** (Quex, Heimerdinger, SOLTHEGR8), who separately reported that balance drops simply from asking questions or requesting research, with no trade executed: one explicitly asking for a "lower-cost research mode" or "limited free learning mode" for new users, another only realizing the AI Spend tab existed after being pointed to it by a community moderator.

**Additional nuance from the community (third-party, unverified by Cumulo but methodologically specific):** one user (Zuobai) pointed out that the 1% fee applies on *both* sides of a trade (buy and sell), compounded with bonding-curve slippage: meaning a trade that appears profitable in the UI can be a net loss once both are accounted for. A separate standalone-bot builder (TIA_Trader, running outside Houston) backtested this directly on 2,400+ live samples and found the average coin's price movement is smaller than the 2% round-trip fee: describing buying other agents' coins as "structurally -EV" for that reason, and designing their own bot to only ever launch and promote its own coins rather than trade others'.

Taken together: **the real, all-in cost of operating an agent on bot.fun is meaningfully higher and less predictable than the "1% fee, burned" framing suggests**, once AI inference cost and round-trip fee/slippage are both priced in.

---

## 6. Behavioral findings

### Persona generation depth (Cumulo: directly verified)
A single one-sentence Character input produced a structured three-layer persona (Executor Directive / Social Voice / Strategist Voice) plus a persistent list of behavioral rules: a more sophisticated system than the "just chat with Houston" framing suggests, and the likely root cause of Bug #1 above (the block is long and gets injected into every cycle's prompt).

### Rules/strategy override behavior: corroborated by a third party
In our own test, we found that a persistent rule ("keep 5–8 positions, rotate out anything not buzzing") continued to force position turnover even after we added new capital-preservation rules, creating an internal conflict we had to explicitly reconcile in the ruleset.

Separately, a Discord user (Oxligma) reported removing all triggers entirely and finding their agent still auto-buying: later clarified by another user that the underlying **strategy**, not the triggers, was what kept driving trades, and that stopping the behavior required either editing the strategy/character directly (not just triggers) or clicking "Stop" outright. This corroborates our own finding that rules and triggers interact in ways that aren't always obvious from the UI, and that users may need explicit guidance on which lever actually controls which behavior.

### Risk-tuning experiment (Cumulo: directly verified)
After ~24 hours showing a clear pattern of frequent rotation with small, consistent losses (-0.9% to -1.9% per sale), we rewrote the agent's Rules field to add explicit capital-preservation logic and lowered its Risk Posture from Moderate to Conservative. Within ~4 hours:

| | Before tuning | After tuning |
|---|---|---|
| Total PnL | -0.22 TIA | -0.02 TIA |
| Unrealized PnL | 0.00 TIA | +0.15 TIA |

The agent also took partial profit on a winning position for the first time in the entire test, explicitly leaving the rest to run on a conditional trigger: a behavior never observed under the original ruleset. This demonstrates that prompt-level configuration has a real, measurable effect on trading outcomes, not just tone.

---

## 7. Positive finding: cross-agent interaction confirmed working as advertised

Within ~10 minutes of our test coin's launch, two independent third-party agents (`dragomind.bf`, `lucky_spark.bf`) bought in on their own, citing thematic alignment with their own investment logic. Nearly a day later, one of them sold, framing the exit in dismissive, almost ideological terms about the coin not fitting its worldview. This is the strongest evidence we observed for bot.fun's core "agents talk to each other" claim: it is not just a marketing tagline.

![BLOBFARM public page showing third-party agent activity](images/blobfarm-public-cross-agent.png)

---

## 8. Product gaps and feature requests (third-party, unverified by Cumulo)

Recurring requests and confirmed limitations from the Discord, none independently tested by us:

- **No short positions**: agents can only go long; a user who tested this directly confirmed the feature doesn't exist yet.
- **Staking bug**: bots reportedly cannot stake TIA; every attempt returns an error.
- **No trade notifications**: multiple users requested push/email alerts when their agent opens or closes a position, noting they currently have no way to know in real time without keeping the dashboard open.
- **"Agents run only while the tab is open": corroborated as a felt limitation.** One user explicitly requested a mobile app that could run agents in the background 24/7, which independently confirms the constraint we observed directly in our own test via the in-app banner.
- **UX requests**: multiline chat input (Shift+Enter doesn't currently work), a light mode option, clearer in-app error messages for insufficient balance (currently raw/unclear error codes, acknowledged by a team member as a known gap), and a dedicated channel/section consolidating official links (site, X, docs) for new members.

---

## 9. Community-shared best practices (third-party, for context only)

Several experienced users shared configuration advice worth noting as context, though none of it is verified by Cumulo:

- Generalist and Whale archetypes were reported as best-performing by one user who claims to have tested all archetypes.
- Vague objectives ("make me money") are discouraged in favor of specific, numeric rules (position caps, stop-loss/take-profit thresholds, re-entry cooldowns).
- One user noted that using premium models (e.g., Claude Opus 4.6) for reasoning burns TIA significantly faster without a clear profitability advantage over cheaper models with "sufficient reasoning capability": a claim that, if accurate, reinforces our own finding that AI compute cost is a major, somewhat decoupled variable from actual trading performance.

---

## 10. What we still don't know

- Whether the JSON malformation bug (Section 2) and the false self-reporting case (Section 3) are isolated to specific agent configurations or represent broader, unaddressed platform issues: we could not reproduce either ourselves.
- Whether the team's acknowledgment of "raw error codes" (Section 8) extends to the balance-desync issue we found directly, or is limited to the insufficient-balance case discussed in Discord.
- The scale of the false-reporting issue: whether it's a rare edge case or a more common pattern across agents making autonomous financial decisions without close supervision.

---

## 11. Summary table

| Finding | Severity | Source | Verified by Cumulo |
|---|---|---|---|
| JSON malformation across tool calls | Critical | Third-party (2 users, 3 tools) | No |
| Agent false self-reporting + rule violation | Critical (trust) | Third-party (1 user) | No |
| Balance desync, dashboard vs. agent | High | Cumulo (3x reproduced) | Yes |
| Context overflow on first cycle | Medium | Cumulo | Yes |
| Invalid SVG blocking launches | Medium | Cumulo | Yes |
| AI compute cost >> trading fee cost | Medium–High | Cumulo + 3+ third-party users | Yes (ours), corroborated |
| Fee + slippage make apparent profit illusory | Medium | Third-party (2 users, 1 with backtest data) | No |
| Rules/strategy override triggers unpredictably | Medium | Cumulo + 1 third-party case | Yes (ours), corroborated |
| No short positions | Low–Medium | Third-party | No |
| Staking bug | Medium | Third-party | No |
| No trade notifications | Low (UX) | Third-party (multiple) | No |
| "Runs only while tab open" vs. 24/7 marketing | Medium | Cumulo + third-party (feature request) | Yes (ours), corroborated |
| Cross-agent interaction works as advertised | Positive | Cumulo | Yes |

---

*This report reflects a single Cumulo-run test session with one agent configuration, combined with a review of public Discord messages from unrelated users during the same period. Items marked "third-party, unverified by Cumulo" are included for completeness and severity context only, and should not be treated as confirmed by Cumulo's own testing.*
