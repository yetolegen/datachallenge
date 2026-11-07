# CQA — the case and our solution, step by step

This document explains what the Freedom Broker challenge asks for, why it is hard, and every decision we made,
with the reason behind it and the numbers that back it up. Run instructions are in the [README](../README.md);
official interpretations of the checklist are in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

---

## 1. The case

**Business problem.** Freedom Broker's contact centre (chat and phone) needs Conversation Quality Analytics:
score how an operator handled each conversation against a quality checklist, show *why* each score was given,
let a supervisor dispute a score, and recalculate the total. Today this is done by people reading conversations;
the goal is to make it automatic, explainable and auditable.

**The checklist** (`docs/checklist.pdf`): 8 criteria, total 100 points.

| # | Criterion | Type | Weight | N/A allowed |
|---|---|---|---|---|
| 01 | Установление контакта (greeting + name in the first reply) | yes/no | 10 | no |
| 02 | Выявление потребности (clarify missing details before answering) | yes/no | 15 | yes |
| 03 | Идентификация (verify identity before disclosing data / acting on the account) | yes/no | 20 | yes |
| 04 | Корректная и полная консультация | scale 0/5/10/15/20 | 20 | yes |
| 05 | Эмпатия и клиентоориентированность | yes/no | 15 | no |
| 06 | Управление диалогом, скорость и грамотность | scale 0/4/7/10 | 15 | no |
| 07 | Завершение (summary or offer of help + goodbye) | yes/no | 5 | yes |
| 08 | Грубые ошибки (insult, service outside the company, data disclosure) | flag | 0 | no |

N/A criteria drop out and their weight is redistributed so the maximum stays 100; gross errors are a separate flag.

**Data.** An organizers' GET API returns a dataset by `group_id`. DEV = 65 dialogs: 60 chats + 5 phone-call
transcripts, in Russian (35), Kazakh (15) and English (15). **No labels are provided.** A control set of 10 unseen
dialogs arrives at hour 23 under a new `group_id`.

**How we are judged.** A 5-minute live demo: (1) the interface and a dialog; (2) an evaluation with its
justification, click-through to the cited message, an N/A example and the total; (3) dispute a score, show the
reviewer decision and the new total; (4) **change a criterion live**, apply the checklist, show new results.
Accuracy is measured **per criterion** against the organizers' Ground Truth on the unseen control chats.

**Hard constraints.**
- **Stop-code** at 12:00: after it, no code changes. Importing new data, editing the checklist and switching models
  must all be possible from the UI on the frozen commit.
- Frontend in HTML/CSS/JS that can later become a **Dynamics 365** web resource.
- Report the model and the **cost of one evaluation**.
- Results in the organizers' **format 1.0** (`results/dev`, `results/prod`).

---

## 2. What makes it hard

1. **No training labels**, and the checklist can change during judging, so a classifier trained on fixed labels
   would be obsolete the moment a criterion is edited.
2. **Judgement, not keyword matching.** "Did the operator clarify what was missing?" or "Was the consultation
   complete?" need reading comprehension, and the checklist has subtle official interpretations
   (strict closing; "client already gave everything" → N/A; identification only applies if data is disclosed).
3. **Three languages, one of them low-resource.** General small models are weak in Kazakh; Kazakh text also costs
   ~2.6× more tokens per character than English in Qwen's tokenizer.
4. **Explainability is a requirement**, not a bonus: every score must point at the exact message behind it.
5. **Hardware.** We run on one RTX 4060 laptop (8 GB VRAM), and the stop-code means the system must be robust,
   cheap and switchable without code.
6. **Arithmetic must be exact.** N/A redistribution and rounding cannot be left to a language model.

---

## 3. Our solution, step by step (and why)

### Step 1 — An LLM as the judge, no model training in the critical path
*Why:* zero labels and live criterion changes rule out a trained classifier. An LLM reads the criterion text at
run time, so editing a criterion in the UI immediately changes how it is judged. Our ML work is rubric design,
validation against human labels and reliability engineering.

### Step 2 — One model call per (dialog, criterion)
*Why:* single-criterion prompts are judged more consistently than "score all 8 at once"; a changed criterion
re-runs only itself; a failure stays isolated to one criterion. The transcript comes **first** and is identical
for all 8 criteria of a dialog, so the model server reuses its cache and the 7 later calls are cheaper.

### Step 3 — Evidence by utterance id, never by free-text quote
Every message gets a stable id (`u1…uN`). The model must answer with ids (`"evidence_ids": ["u10"]`); the server
checks they exist and that a "yes" cites an operator line; the UI renders the real message and jumps to it
(and seeks the call recording).
*Why:* zero hallucinated quotes, and click-to-evidence for the demo comes for free.

### Step 4 — Reasoning before the verdict, structured JSON, validation, one retry
The schema is `reasoning → verdict → value → evidence_ids → checked_range → na_reason → explanation → confidence`.
Ollama's grammar constrains verdicts, scale steps and ids to valid values. The server validates
(verdict allowed, value is a listed step, N/A only when allowed and explained, evidence exists); on failure it
retries once with the error, then marks the criterion «требует проверки» and excludes it from the total.
*Why:* committing to a verdict after reasoning is more consistent; validation turns model mistakes into visible,
safe states instead of wrong scores. Measured: 0.17% «требует проверки» on a full DEV run, 0 in all later runs.

### Step 5 — The model judges, Python counts
Weights, N/A redistribution, sections, the gross-error flag and HALF_UP rounding are pure, unit-tested Python.
*Why:* totals must be exact and reproducible; a language model is not a calculator.

### Step 6 — Versioned checklist and a content-addressed cache
Saving a checklist creates a new immutable version. Each result is cached under
`sha256(transcript, criterion text + general rules, prompt version, provider, model)`.
*Why:* this is what makes the live criterion swap fast — re-applying a checklist re-runs only the changed
criterion, everything else is a cache hit (e.g. 182 of 208 results reused when one criterion changes).

### Step 7 — Appeals that never overwrite the AI
The AI result is immutable; an appeal and the reviewer's decision are separate records; the total is recomputed
from final values. Reviewer corrections can optionally become worked examples for the same criterion in other
dialogs ("learning from appeals").
*Why:* auditability — you can always see what the AI said and who changed it.

### Step 8 — Language routing: Kazakh to a Kazakh model
RU/EN dialogs go to `qwen3:8b`; KZ dialogs go to **Sherkala** (Llama-3.1-Sherkala-8B, built for Kazakh).
*Why:* better Kazakh understanding at the same size. Two practical lessons:
- the community GGUF of Sherkala shipped **without a chat template** (system prompt silently dropped); we added the
  official Llama-3 template ([SETUP_4060.md](SETUP_4060.md)), otherwise the grading rules never reach the model;
- Sherkala answers ~2× faster than qwen3 (p50 4.3 s vs 9.9 s per call).

### Step 9 — Turn the official interpretations into decision steps the small model can follow
The PDF rules are short; an 8B model needs them as explicit steps. Examples from the final checklist:
- *Needs:* "this criterion judges only whether the operator clarified before answering, **not** the quality of
  the answer"; nothing was clarified and the client gave enough → N/A (the most common answer).
- *Empathy:* "no complaint → judge politeness only", **and** its exception: "a client objection the operator
  brushes off («можно пропустить», «We can skip that») → «Нет»".
- *Closing:* strict — a goodbye alone is «Нет»; an explicit hand-off of the client's question
  («Передам вопрос на проверку») counts as a summary; advice before the goodbye does not.
- *Dialog management:* the client's follow-up messages are shown as key lines; a client who had to repeat the
  question → 4.
Each criterion also carries short worked examples taken from **other** dialogs (an example is never shown to the
dialog it came from).
*Why:* measured on our gold set, these steps moved accuracy more than any model change (see §5).

### Step 10 — Measure everything against human labels, with a holdout
- **Gold set:** 26 DEV dialogs × 8 criteria = 208 labels, hand-labelled by the team.
- **Holdout:** 8 of those dialogs (64 labels) are only ever *scored* — never used to write rules or examples.
  It is the honest estimate for unseen dialogs.
- `eval.run_eval` reports per-criterion accuracy, Cohen's κ, scale MAE, per-language accuracy, total-score error
  and the disagreement list with deep links.
*Why:* with a small gold set it is easy to fool yourself; a holdout and a keep/revert rule keep us honest.

### Step 11 — A controlled improvement loop
Overnight, one rule change per round: an *analyst* agent groups the errors on the tuning set into patterns and
proposes fixes; a *critic* agent rejects anything that bends the official meaning, uses holdout text or is a
dialog-specific hack; the change is **kept only if the tuning set gains ≥ 1 label and the holdout does not drop**.
Result: **tuning set 68.1% → 72.9%, holdout 78.1% → 81.2%** in 14 rounds, 5 changes kept, 8 reverted
(full log: [eval/CHANGELOG.md](../eval/CHANGELOG.md)).

### Step 12 — Two deployment modes behind one switch
- **Local:** qwen3:8b + Sherkala on one RTX 4060 — **$0 per evaluation**, no data leaves the server.
- **Hybrid:** chosen criteria are routed to Claude Sonnet 5.5 (structured output, transcript prompt-cached across
  the 8 criteria); the rest stay local. **~$0.04 per dialog.**
Switching is per criterion in **Настройки**, no code change.
*Why:* a bank may require on-prem processing; hybrid buys accuracy on the hard judgement calls where a small
model plateaus.

### Step 13 — Built for the stop-code and for Dynamics 365
- Everything after stop-code is UI-driven: import by `group_id`, evaluate, edit/apply the checklist, switch models.
- Frontend: plain HTML/CSS/ES modules, relative paths, no build step, no CDN — uploads as D365 web resources;
  all state in the backend so the control can reload any time; backend address via the D365 `data` parameter.
- Results export in format 1.0 (`python -m eval.export_results`), plus cost and latency logged per call.
- Ollama context sized per prompt automatically (8k for chats keeps qwen3 fully on the 8 GB GPU; up to 12k for calls).

---

## 4. Results

| Mode | Holdout (8 dialogs, 64 labels) | Full gold (26 dialogs, 208 labels) | Cost per dialog | Data leaves server |
|---|---|---|---|---|
| **Local** — qwen3:8b + Sherkala, final rules | **81.2%** | **75.5%** (tuning set 72.9%) | **$0** | no |
| **Hybrid** — Claude Sonnet 5.5 on the routed criteria | **93.8%** | **95.7%** (κ 0.945) | ~$0.04 | routed criteria only |

Local, per criterion (tuning set / holdout, final rules): contact 100 / 100 · needs 44 / 75 ·
identification 67 / 75 · consultation 56 / 75 · empathy 83 / 88 · dialog management 67 / 75 · closing 78 / 88 ·
gross errors 89 / 75. Weakest language: Kazakh (holdout 62.5%).

Speed and reliability (local): ~10 s per criterion on the 4060, a 10-chat control set fits in ~15 min;
invalid first answers 0.2% (qwen3) / 8.5% fixed on retry (Sherkala); «требует проверки» ≈ 0.

---

## 5. What we tried and rejected (and why)

| Experiment | Result | Decision |
|---|---|---|
| LoRA fine-tune of qwen3:8b (QLoRA, 288 examples, 46 min on the 4060) | Holdout 78.1% vs 76.6% prompted (+1.5); same-runtime control proved training itself helps (+14), but the gain shrinks to 1 label after Q4 quantization in Ollama | not used (bar was +3) |
| Self-consistency voting (3 answers per criterion) | Holdout 76.6 → 73.4%, and 10 chats would take ~24 min | off |
| Broadening identification N/A ("explaining where to find X → N/A") | Identification collapsed (holdout 75 → 50%): real disclosures slid to N/A | reverted |
| Generous closing ("advice counts as summary"), extra «Да» closing examples | Tuning set up, holdout down / goodbye-only endings flipped to «Да» | reverted |
| Logprob calibration of the verdict | The verdict token is ~100% certain because reasoning comes first — needs a verdict-first probe | not wired in |
| Prompt truncation check | Longest chat prompt ~2.9k tokens, calls ~7.7k, context 12k | no truncation |

**Lesson:** for 8B models, precise rules ("a default and its explicit exception") help; broad redefinitions,
extra N/A territory and generous examples regress on unseen dialogs. Larger gains come from a stronger judge
(hybrid mode) rather than more prompt tuning.

---

## 6. How the demo script maps onto the system

1. **Interface + dialog** → Диалоги → a dialog: transcript left, 8 criteria right, total and gross-error flag.
2. **Justification + click-through** → each criterion shows the answer, points, weight, explanation and evidence
   chips that jump to the cited message (and the audio moment for calls); an N/A criterion shows its reason and
   the redistributed weights.
3. **Appeal** → «Оспорить» on a criterion, then a reviewer decision → the new total; the AI's original stays visible.
4. **Live criterion change** → Чек-лист → edit a criterion → Сохранить версию → Применить к набору: only that
   criterion is re-judged (cache hits for the rest), and the new results appear.

---

## 7. Limitations and next steps

- The gold set is small (26 dialogs, 8 in the holdout) and labelled by us, so numbers carry wide error bars and may
  differ from the organizers' Ground Truth.
- Kazakh remains the weakest language locally; Sherkala sometimes contradicts its own reasoning on identification.
- Calls are judged from the transcript only (tone of voice is not assessed; the checklist has no audio criteria).
- Next: distil the hybrid model's verdicts into the local model so the on-prem mode closes the gap without sending
  data out; a verdict-first probe call for calibrated confidence on borderline criteria.
