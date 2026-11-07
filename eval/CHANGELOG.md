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
