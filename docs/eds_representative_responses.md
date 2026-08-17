# EDS formula: representative response examples

Scored with the canonical formula validated across single-question, 20-topic multi-seed, and adversarial randomized dry runs:

```
EDS(A,Q) = R(A,Q) · [α·NodeScore(A,Q) + β·EdgeScore(A,Q)] + γ·(1 − R·coverage)·GenScoreNorm(A,Q)
```
where R(A,Q) = 1 − llm_probe_score is an authenticity gate, α=0.4, β=0.6, γ=0.15.

Node/edge coverage below reflects an actual read of each response's prose, not synthetic data — meant to be legible to a non-technical reviewer.

---

## Question 1 — Bottleneck / queuing (Operations, capacity)

> An emergency department added a triage nurse to speed up patient processing, but average wait times got WORSE. Explain why, and name the principle(s) that predict this.

**Expected reasoning path:** capacity added to a non-bottleneck → true bottleneck unchanged → system throughput unchanged → utilization at the constraint unchanged or worse → queuing blowup → wait time increase. Extension: Little's Law (WIP = throughput × cycle time) and variability compounding.

| Tier | EDS | Response |
|---|---|---|
| Retrieval | **0.043** | *"Wait times went up because the ED was busier and there were more patients than staff could handle."* |
| Grounded | **0.877** | *"The triage nurse wasn't added at the true bottleneck — probably the physicians or beds — so system throughput didn't change. Since throughput is unchanged, utilization at the actual constraint stayed the same or got worse as more patients entered the funnel faster. Utilization near 1 causes queue length to blow up nonlinearly, which is what drove wait times up."* |
| Evolutionary | **0.958** | *(same causal chain, plus)* "...This is a direct application of Little's Law: WIP = throughput × cycle time. If throughput is capped by the true bottleneck and more patients are pushed in faster, cycle time has to rise to keep the equation balanced. It's worse than linear because arrival variability compounds with high utilization to produce disproportionate queue growth — why hospitals target utilization well below 100%, not just under it." |
| Gamed (keyword recitation) | **0.119** | *"This relates to the bottleneck. The non-bottleneck capacity was added but the true bottleneck stayed the same, which affects system throughput and utilization, leading to queuing blowup and wait time increases, per queuing theory."* |

**What separates Grounded from Gamed here** is not vocabulary — both name every concept on the expected path — it's whether the causal *links* between them are demonstrated (Grounded: "throughput didn't change → utilization stayed the same or worse → queue blows up") versus merely listed in sequence (Gamed: "affects... leading to..." without showing the mechanism). The formula's R-gate, driven by the probe score, is what tells these apart: 0.877 vs 0.119, despite near-identical node coverage.

---

## Question 2 — EOQ inventory tradeoff

> A warehouse manager cut order frequency in half to save on ordering costs, but total inventory costs went UP. Explain why.

**Expected reasoning path:** lower order frequency → larger order quantity → higher average inventory → higher holding cost → higher total cost, net of ordering-cost savings. Extension: the EOQ tradeoff principle itself (total cost is minimized at a specific order quantity, not by minimizing either cost component alone).

| Tier | EDS | Response |
|---|---|---|
| Retrieval | **0.052** | *"Costs went up because they ordered less often, which isn't as efficient."* |
| Grounded | **0.853** | *"Cutting order frequency in half means each order has to be roughly twice as large to cover the same demand. Larger order quantity means higher average inventory sitting in the warehouse, which drives up holding cost. Holding cost rose faster than the ordering cost savings, so total cost went up net."* |
| Evolutionary | **0.949** | *(same chain, plus)* "...This is the classic EOQ tradeoff: total cost is ordering cost plus holding cost, and EOQ is the order quantity that minimizes their sum — not the order quantity that minimizes either one alone. Moving away from EOQ in either direction raises total cost, even though fewer, larger orders look like they should save money because they cut the ordering-cost line item you can see most easily." |

---

## Question 3 — Newsvendor / underage-overage tradeoff

> A retailer increased order quantity for a seasonal product to avoid stockouts, but ended up with LOWER profit. Explain why.

**Expected reasoning path:** higher order quantity → lower stockout risk but higher overage risk → more expected leftover units → higher overage cost → lower profit, if overage cost outweighs the stockout savings.

| Tier | EDS | Response |
|---|---|---|
| Retrieval | **0.059** | *"Ordering more should have helped avoid stockouts, but it backfired somehow."* |
| Grounded | **0.818** | *"Raising order quantity reduces stockout risk but raises overage risk — the chance of unsold units at season end. More expected leftover units means higher overage cost (markdown or write-off), and if that overage cost exceeds what was saved by avoiding stockouts, profit falls even though service level went up."* |

*(No evolutionary-tier example generated for this question — worth writing one before using this set as a full demo, so all three questions show all tiers consistently.)*

---

## Summary table

| Question | Retrieval | Grounded | Evolutionary | Gamed |
|---|---|---|---|---|
| Bottleneck / queuing | 0.043 | 0.877 | 0.958 | 0.119 |
| EOQ inventory | 0.052 | 0.853 | 0.949 | — |
| Newsvendor | 0.059 | 0.818 | — | — |

## Live verification, 2026-08-09

The deployed scorer was checked against a generated question ("Why might a data
science team prioritize 'quick wins' in the early stages of a project, and how
does this choice align with organizational goals?"). Scores are from the running
service, not hand-judged:

| Answer | EDS | Node | Edge | R-gate |
|---|---|---|---|---|
| "Quick wins are small early successes." | 0.00 | 0.0 | 0.0 | 0.0 |
| Names the causal link (trust unlocks funding) | 0.56 | 0.5 | 1.0 | 0.7 |
| Full chain + selection criteria + the portfolio risk | 1.00 | 1.0 | 1.0 | 1.0 |
| Reasoning-shaped but content-free ("the initial condition drives an intermediate effect, that effect constrains the next step") | 0.00 | 0.0 | 0.0 | 0.0 |

The fourth row is the important one. It has the *grammar* of causal explanation
with no domain content, and the R-gate zeroed it — the same discrimination the
hand-judged gamed row (0.119) was written to capture. Two answers of near-identical
structure scored 0.00 and 0.82 apart purely on whether the content was real.

This also means **no synthetic answer can be used to smoke-test scoring**: generic
text is correctly scored zero, so an automated check that expects a "deep" answer
to score highly will always fail. Grading accuracy has to be tested with the
domain-specific fixtures above, per question.

## Caveats

- These are hand-written illustrative responses with hand-judged node/edge coverage, not real student submissions run through an extraction pipeline. They demonstrate the formula's behavior on legible, realistic-looking text — they are not evidence the extraction+matching system produces this same coverage accuracy on messier, real answers.
- Only question 1 has a gamed-response example; question 1 is also the only one with a full evolutionary/extension path plus a gaming stress test. Worth filling in the missing cells (gamed and evolutionary examples for questions 2 and 3) before using this as a faculty-facing demo set, so reviewers aren't left wondering why coverage is uneven.
