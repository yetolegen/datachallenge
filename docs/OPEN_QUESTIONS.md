# Open questions and our interpretations

## Ask the organizers

1. How is accuracy against Ground Truth measured: per-criterion answer match, total-score difference, or both?
2. Will the 10 control transcripts include calls? Does the same 8-criteria chat checklist apply to calls?
3. Is Ground Truth made with the original checklist or with the replaced criterion?
4. Can we get a reference evaluation of 2–3 DEV dialogs for calibration?

## Decisions taken from the checklist PDF (not guesses)

- Scoring: yes = full weight, no = 0, scale proportional with the top step = full weight;
  N/A weight redistributed proportionally; max stays 100; total rounded to **2 digits** (HALF_UP);
  no applicable weighted criteria → total not computed.
- Gross errors (criterion 08): weight 0, shown as a separate flag, does **not** change the total.
- Scale criteria accept only the listed steps (04: 0/5/10/15/20, 06: 0/4/7/10).
- First-response 3-minute threshold, tags and attachments are not scored (p.2).
- No criterion needs audio, so there is no audio-only N/A logic.

## Our interpretations (change in the checklist UI if the organizers disagree)

- "Первый ответ" = the operator's first message after joining; the bot's greeting doesn't count.
- A masked name (`[Имя скрыто]`) counts as the operator introducing themselves.
- A system message like «Личность клиента подтверждена» is valid evidence for identification.
- Details the client wrote to the bot before escalation count as "known details" for criterion 02.
- Calls are judged with the same checklist (the brief gives one checklist for all dialogs).
- Section results are shown as points out of the section's redistributed maximum.
