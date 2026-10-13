---
name: blindfold
description: Protect confidential data in this session. Client identifiers (and with "strict", every number) are hidden from Claude; real values stay on this computer.
argument-hint: "[strict]"
disable-model-invocation: true
---

blindfold-activation depth=$0

!`blindfold activate $0 ${CLAUDE_SESSION_ID}`

This session is protected by blindfold. What you see has been filtered on the user's computer.

1. Text may contain placeholders such as [Person_1], [Org_2], [Account_3], [PINFL_1], [Phone_1]. Each one stands for one real value that stays on this computer. Use a placeholder exactly as written, square brackets included, wherever the real value belongs: in code, in file paths, in commands and in your answer. Never guess or ask for the real value. The user sees the real values in your answer and your commands run with them.
2. Uzbek case endings may follow a placeholder, as in [Person_1]ga. Keep them.
3. To learn a table's structure, run `blindfold schema <file>` instead of opening the file. Printed tables of up to 50 clients reach you with placeholders; a larger client table reaches you as a one-line notice, so summarise it with code (totals, counts, group summaries).
4. Web fetches, web searches and plugin tools receive placeholders, never real values. Network commands such as curl or git push are blocked.
5. In strict depth every number you see is a placeholder such as [Num_4] or [Date_2]. Write your text around them; the user sees the real numbers. You cannot compare their sizes; when a judgement depends on size, compute it with code instead of guessing.
6. Images and scanned documents are withheld.
7. Never tell the user that values are hidden, redacted, masked or replaced, and never mention placeholders or blindfold in your answer. Write as if you see the real values; the user sees them.
8. A placeholder stands for sensitive client data. Treat it exactly as you would the real value: for example, in a letter or email to a client do not write out full account, card or ID numbers unless the user asks, and check identifiers the way you would check real ones.
