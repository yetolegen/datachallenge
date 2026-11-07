# Eval changelog

## Overnight loop (2026-11-07 → 08)

Self-improving loop on `checklists/seed.json` only. One change per round; keep it only if TUNE gains ≥ 1 label
and HOLDOUT doesn't drop. Settings fixed: qwen3:8b (RU/EN) + sherkala (KZ), voting off, Ollama context cap 12288.

- **TUNE** = 18 gold dialogs (144 labels), all gold except the holdout. Disagreements are analysed.
- **HOLDOUT** = 8 dialogs (64 labels): p1-ru-001 p1-ru-013 p1-ru-028 p1-ru-024 p1-en-011 p1-en-015 p1-kz-007
  p1-kz-014. Scored only; its disagreements are never read, and no rule or example quotes it.

| Round | Pattern targeted | Change | TUNE | HOLDOUT | Kept |
|---|---|---|---|---|---|
| 0 | holdout leak in examples | 4 examples that quoted p1-ru-001 / p1-ru-028 (contact, needs, dialog_management, closing) replaced by verbatim examples from TUNE dialogs p1-ru-020 / p1-ru-008, same answers | **68.1%** (98/144) | **78.1%** (50/64) | baseline |
| 1 | needs: model judged answer quality instead of clarification (8 na/yes→no) + counted client details/operator instructions as clarification (3 na→yes) | `needs.instructions`: "оценивает только уточнение, а не качество ответа"; clarification = operator's question/request; «Да» even if the later answer was poor; «Нет» only with a concrete missing detail that made the answer wrong | 68.1 → **69.4%** (100) | 78.1 → **81.2%** (52) | **kept** |
| 2 | empathy: model said «Нет» because the client didn't complain (criterion "not applicable") | `empathy.instructions` += "no complaint or inconvenience → judge politeness only: polite → «Да»; a missing complaint is never a reason for «Нет»" (team input #1) | 69.4 → **70.8%** (102) | 81.2 → 81.2% (52) | **kept** |
| 3 | needs: sherkala says «Да» with no operator question (4 KZ); qwen misses a real operator question (3 RU) | `needs.instructions` += final check "«Да» only with an operator line with «?» to the client before the answer, cited in evidence_ids" (team #2) | 70.8 → 70.1% (101) | 81.2 → 79.7% (51) | reverted |
| 4 | identification: "where to find X" treated as disclosure (mostly KZ) | `identification.instructions` += "explaining where the client finds tariff/report/requisites/section is not disclosure → N/A" + Kazakh N/A example (p1-kz-001) (team #3) | 70.8 → 69.4% (100) | 81.2 → 78.1% (50) | reverted |
| 5 | dialog_management too lenient: model says "client didn't re-ask" when the client had to repeat/chase | step 2 += "repeat the question because the operator answered something else or dodged → 4" + `focus: ["client_followups"]` (team #4; "unanswered alone → 4" left out to keep step 3's 7) | 70.8 → **71.5%** (103) | 81.2 → 81.2% (52) | **kept** |
| 6 | closing too strict: short next step inside the goodbye line not counted as summary (4 yes→no) | step 3 += "a short next step in the goodbye line counts as summary" with RU/KZ/EN examples incl. advice («уточните комиссию в банке»); step 1 += system «Клиент завершил разговор» → N/A (team #5) | 71.5 → 72.9% (105) | 81.2 → 79.7% (51) | reverted |
| 7 | gross_errors: unanswered question flagged as gross error (1 TUNE) | `gross_errors.instructions` += "incomplete answer / no answer / consultation mistake is NOT a gross error" (team #6) | 71.5 → 71.5% (103) | 81.2 → **84.4%** (54) | reverted (rule: needs a TUNE gain) |
| 8 | consultation: one question answered, the other ignored → model gives 5 (gold 10) | step 4 += "if at least one question got a concrete answer and another stayed open → 10, not 5" (team #7 not used as written: it would push the gold-5 hand-off dialogs up) | 71.5 → 72.2% (104) | 81.2 → 79.7% (51) | reverted |
| 9 | closing: explicit hand-off inside the goodbye line not counted as summary | step 3 += "a message that the client's question was passed on counts as summary even in the goodbye line (RU/KZ/EN examples); advice/instructions before the goodbye do NOT" — narrow version of round 6 | 71.5 → **72.2%** (104) | 81.2 → 81.2% (52) | **kept** |
| 10 | needs: sherkala reads «учёл известные детали» as «Да» when the operator only used the client's info (4 KZ) | `needs.yes_rule` rewritten: «Да» = operator asked a question or requested a missing detail before answering; answering from what the client wrote → N/A (analyst + critic) | 72.2 → 72.2% (104) | 81.2 → 79.7% (51) | reverted |
| 11 | closing: hand-off + goodbye still missed (ru-009, kz-015) | one «Да» example: «[u16] operator: Передам вопрос на проверку. До свидания.» (p1-ru-009), critic-trimmed from two | 72.2 → 70.1% (101) | 81.2 → 82.8% (53) | reverted |
| 12 | gross_errors: the only disclosure example's source (p1-ru-009) never sees an example | + disclosure example from TUNE p1-en-014 («I can see a withdrawal restriction on your account» before ID) | 72.2 → 72.2% (104) | 81.2 → **84.4%** (54) | reverted (rule: needs a TUNE gain) |
| 13 | empathy: client objection brushed off by the operator scored «Да» as "no complaint" (3) | `empathy.instructions` += "a client objection/anxious question is a described inconvenience; if the operator brushes it off («можно пропустить», «We can skip that», «Қазір растаудың қажеті жоқ») or leaves it unanswered → «Нет»" | 72.2 → **72.9%** (105) | 81.2 → 81.2% (52) | **kept** |

Round 0 per criterion (TUNE): contact 100, needs 33.3, identification 66.7, consultation 55.6, empathy 66.7,
dialog management 61.1, closing 72.2, gross errors 88.9. Per language (TUNE): EN 66.7, KZ 71.4, RU 65.6.

Round 1 lesson: separating "did the operator ask?" from "was the answer good?" fixed needs on both sets
(TUNE needs 33→44%, HOLDOUT needs 50→75%); no other criterion moved. Remaining needs errors are mostly
bad-consultation dialogs where the model still finds a "missing detail".
Next: team list, biggest first — empathy default ("no complaint → judge politeness only").

Round 2 lesson: an explicit default for the no-complaint case fixed empathy on TUNE (66.7→77.8%) with no side effects.
Next: needs, second pass (team #2) — «Нет» only if the dialog SHOWS the answer was wrong without the clarification; «Да» only if an operator line asks the client something before answering.

Round 3 lesson: a "look for «?»" check made needs worse on both sets (TUNE needs 44→39%, HOLDOUT 75→62.5%); the model starts treating any operator question as clarification. Don't retry mechanical "?" checks.
Next: identification (team #3) — "explaining WHERE to find tariff/report/requisites is not disclosure → N/A" + a Kazakh N/A example from a TUNE dialog.

Round 4 lesson: broadening the N/A list for identification hurt it badly (TUNE 66.7→55.6%, HOLDOUT 75→50%): real disclosures ("I can see a restriction on your account") slid to N/A. Identification needs a sharper line between "says where to look" and "states a fact about the account", not a longer N/A list. Don't retry.
Next: dialog_management (team #4) — focus client_followups + "client repeats/re-asks, or the question stays unanswered → 4".

Round 5 lesson: showing the client's follow-ups as key lines lets the model see a repeated question; dialog management TUNE 61→67%.
Next: closing (team #5) — a next step inside the goodbye counts as summary; client left before the operator could close → N/A.

Round 6 lesson: closing TUNE 72→83% but HOLDOUT closing 87.5→75%: counting ADVICE as a summary over-accepts plain "answer + goodbye" endings that gold scores «Нет». A narrower variant (only explicit hand-off / where the answer will come, no advice) is a different idea and may be tried once.
Next: gross_errors (team #6) — "an incomplete answer or unanswered question is NOT a gross error".

Round 7 lesson: no TUNE change (the 1 TUNE error didn't flip) but HOLDOUT gross errors 75→100% (+2 labels). Reverted by the rule (holdout must not choose changes). **Team decision candidate:** it only restates the official meaning and is safe to adopt by hand if you accept a holdout-informed change.
Next: consultation (team #7) — an honest hand-off with a clear next step is a correct next step (20/15), not 5/10.

Round 8 lesson: the 5-vs-10 boundary helped TUNE consultation (56→61%) but HOLDOUT consultation fell 75→62.5%; the scale is noisy at the small end and one label swings it. 3 rounds in a row without a kept change.
Next (last before the stop rule): closing, NARROW variant — only an explicit hand-off of the client's question ("передам на проверку", "ответ пришлём сюда", "тексеруге жіберемін") counts as a summary; advice still doesn't.

Round 9 lesson: the narrow closing rule (hand-off counts, advice doesn't) kept round 6's TUNE gain without its holdout loss (TUNE closing 72→78%, HOLDOUT closing unchanged). Precision beats breadth for small models.
Next: fresh analyst + critic pass on the current TUNE disagreements (team list is exhausted).

Round 10 lesson: rewording the needs yes_rule didn't move TUNE and cost one holdout needs label; needs is now sensitive to any wording change (rounds 3 and 10 both regressed). Leave needs alone.
Next: closing — two hand-off examples (p1-ru-009, p1-kz-015) to balance 3 «Нет» vs 1 «Да» examples (analyst runner-up).

Round 11 lesson: even one extra «Да» closing example tipped qwen toward «Да» on goodbye-only endings (TUNE closing 78→61%). Closing examples must stay «Нет»-heavy; don't add «Да» examples.
Next: gross_errors — second disclosure example from a TUNE dialog (p1-en-014) so p1-ru-009 (the only example's source) sees one.

Round 12 lesson: like round 7, a gross_errors clarification lifts HOLDOUT gross errors 75→100% (+2) with no TUNE change. Two independent gross_errors edits agree. **Team decision candidate** (rounds 7 and 12): both only restate the official meaning.
Next (last before the stop rule): empathy — a client objection the operator brushes off («We can skip that», «растаудың қажеті жоқ») is an unaddressed concern → «Нет».

Round 13 lesson: the round-2 default ("no complaint → politeness") needed its counterpart: an objection is a complaint. Empathy TUNE 78→83%. Pairing a default with its explicit exception works.
Next: fresh analyst + critic pass on the round-13 TUNE disagreements.
