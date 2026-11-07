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

Round 0 per criterion (TUNE): contact 100, needs 33.3, identification 66.7, consultation 55.6, empathy 66.7,
dialog management 61.1, closing 72.2, gross errors 88.9. Per language (TUNE): EN 66.7, KZ 71.4, RU 65.6.

Round 1 lesson: separating "did the operator ask?" from "was the answer good?" fixed needs on both sets
(TUNE needs 33→44%, HOLDOUT needs 50→75%); no other criterion moved. Remaining needs errors are mostly
bad-consultation dialogs where the model still finds a "missing detail".
Next: team list, biggest first — empathy default ("no complaint → judge politeness only").
