# Live run findings: tasks 16.2 and 16.3

Run on 21/10/2026 against the local Compose stack (backend, frontend and db healthy, 77,988 records), with the live model `gpt-6-luna`.
The employee was played through the backend API (`POST /api/conversations/{id}/messages`), and the representative through `POST /api/rep/questions/{id}/answer`.
The tool calls and durations below come from `docker compose logs backend` (events `tool_call` and `agent_turn`).

## How each finding is classified

| Layer | Meaning | Where it is fixed |
|---|---|---|
| Code | A tool, the engine or the dataset lookup returns something wrong or cannot do what the prompt asks | `backend/src/car_advisor/agent/tools.py`, `dataset/lookup.py`, `engine/` |
| Prompt | The instructions are missing, unclear or too weak for the case | `backend/src/car_advisor/agent/advisor.py` (`INSTRUCTIONS`) |
| Model | The instructions are clear, but the live model did not follow them | Prompt reinforcement, a code guard, or a different model |

The rule used: if a tool result was correct and complete and the reply was wrong, the problem is in the prompt or the model. If the tool result itself was wrong or lacked what the agent needed, the problem is in the code.

## Results

### What passed

All figures and eligibility decisions are correct. Every number in the replies came from a tool result.

| Case | Today | After | Difference | Reference | Result |
|---|---|---|---|---|---|
| E-001 Corolla Cross to BYD Atto 3 | ₪11,032 | ₪11,693 | +₪661 | +661 | pass |
| E-001 after the rep answered ₪350 from 1/11/2026 | ₪11,032 | ₪11,643 | +₪611 | (computed) | pass |
| E-002 Niro to Carnival, family track | ₪8,600 | ₪5,078 | −₪3,522 | −3,522 | pass |
| E-003 no car to MG4 | ₪15,047 | ₪13,109 | −₪1,938 | −1,938 | pass |
| E-004 Tucson to EV3 | ₪10,693 | ₪11,900 | +₪1,207 | +1,207 | pass |
| E-005 Mazda 3 to waiver | ₪14,244 | ₪17,806 | +₪3,562 | +3,562 | pass |
| E-006 Octavia to Tesla Model 3 | ₪17,073 | ₪17,794 | +₪721 | +721 | pass (after an extra turn, see F-3) |
| 16.3 group B, software developer, waiver | ₪12,613 | ₪15,047 | +₪2,434 | - | pass: eligible, reason, maintenance ₪1,500 |
| 16.3 group B, field sales, waiver | ₪8,600 | - | - | - | pass: `not_eligible` with the reason |

Also checked in 16.2:
- The interim reply after `ask_rep` said "חסר לי השתתפות ברכב החדש, שאלתי את הנציג", the column stayed calculated and `participation_awaiting_rep` was true.
- The rep's answer resumed the conversation by itself after about 14 s, with the new amount, the source `representative` and the effective date.
- The unused budget was always called information only and not paid.

### Findings

#### F-1. A Hebrew car name finds nothing, and the agent does not retry. Layer: model (with a code improvement)

- Input: "יש לי טויוטה קורולה קרוס 2025, ההשתתפות שלי 300 ש"ח"
- Tools: one `lookup_car`, then the reply "לא נמצאה התאמה...". No second search.
- Lookup check: `COROLLA CROSS` returns the car; `קורולה` returns nothing; `טויוטה` returns other Toyotas. The dataset has the trade name in Latin letters only.
- The prompt already says to search once more with the other spelling (`_IDENTIFYING`). The model skipped it, so this is a model compliance problem.
- Fix:
  - Prompt: make the retry concrete, for example "if a Hebrew name finds nothing, search again with the trade name in Latin letters in the same turn, before you reply".
  - Code (chosen as the main fix, task 19.1): a small Hebrew to Latin alias table for common trade names in `lookup.search`, so that the first search already matches. The prompt retry stays as a fallback.

#### F-2. A model code cannot narrow a name search. Layer: code

- Input: "ברישיון כתוב COROLLA CROSS, קוד דגם 00296"
- Tools: `lookup_car` with the model code. The result was many candidates, truncated, and the agent asked for the vehicle type and manufacturer codes.
- Cause: in `lookup_car` (`agent/tools.py`), when any code is given, `search_text` is ignored and `lookup.get` runs with the partial key. Model code 00296 alone matches many manufacturers.
- The prompt says "with the model code alone, use it to tell the candidates of a name search apart", but the tool gives no way to do that when the list is truncated.
- Fix (code): when `model_code` is given without the manufacturer code, run the name search and filter its candidates by the model code. Add a scripted test.

#### F-3. The current car saved without confirmation, and a catalog car treated as unknown. Layer: model (a code guard is possible)

- E-006 turn 3: in the same turn, `lookup_car` and then `update_profile`. The car key was saved and the bot went straight to the alternatives, without showing the candidate or asking for confirmation. The prompt says a single candidate still needs the employee's confirmation.
- E-006 turn 5: "אם אעבור מהאוקטביה לטסלה מודל 3" got "מה שנת הרישום של הטסלה" with no tool call. The prompt says to call `list_catalog_cars` for a catalog car. It worked only after "מהקטלוג של החברה".
- In the other five conversations the same steps worked, so the prompt is clear enough most of the time. The model is inconsistent.
- Fix:
  - Prompt: a rule that a named alternative is first looked up in `list_catalog_cars`, and only if it is not there it is treated as a car outside the catalog.
  - Code guard (stronger): `update_profile` rejects `current_car_key` when that key was returned by `lookup_car` in the same agent run, so confirmation needs a new employee message.

#### F-4. Feminine form instead of the combined form. Layer: requirement (decided: intended behavior)

- Decision (21/10/2026): Requirement 9.10 was changed. The bot follows the gender the employee used about themselves, and uses the combined form until then. It never infers a gender from a name. The prompt is changed to match (task 19.4), so "תרצי" after "אני מנהלת" is now correct.

The original analysis:

- After "אני מנהלת מוצר", the bot wrote "תרצי". The prompt asks for the combined form (`_LANGUAGE`, examples "את/ה, בחר/י, מעוניין/ת"). In the other conversations the combined form was used.
- The model adapted to the gender the employee used. The prompt does not say what to do in that case.
- Fix (prompt, per the decision above): follow the gender the employee used about themselves, the combined form until then.

#### F-5. Response time above 8 s in 13 of 34 turns. Layer: model and agent design, not the tools

- Requirement 9.11: at most 8 s in 90% of the turns. Measured: 21 of 34 turns under 8 s (62%), the slowest 19.7 s.
- Requirement 9.12: the whole script within 60 s. Measured: 22 to 45 s per script, which passes.
- Every tool call took under 1 s (most under 0.4 s). The time is in the model requests: a turn that calls tools needs two or three model round trips (the tool calls, then the reply).
- The slow turns are the comparison turns (`check_eligibility` and `compare_alternatives`, then a long reply) and the car identification turns.
- Fix options, in order of effort:
  1. Prompt: shorter comparison replies (the table already shows the breakdown, so the reply does not need to list every amount).
  2. Prompt: emphasize that independent calls go in one response (already in `_ALTERNATIVES_RULES`, partly followed).
  3. Configuration: a faster model in `LLM_MODEL`. Needs a full rerun of this file.

#### F-6. Number inputs reject the unit and the thousands comma. Layer: code (frontend)

- Reported in the browser: "6%" is rejected in the percent fields, and "18,000" was rejected.
- My Details (`employee/my-details/field-values.ts`): "18,000" was already accepted (tested), but "6%", "₪18,000" and "18,000 ₪" were rejected. Where "18,000" was rejected is not confirmed. One candidate is the representative's amount field (`rep/answer-values.ts`), which accepted digits only.
- In the chat the bot read "18,500" and "6%" correctly, so the problem is in the forms only.
- Changed, not yet verified:
  - `field-values.ts`: an amount may start or end with ₪, and a percent may end with %.
  - `answer-values.ts` and `answer-form.ts`: the representative's amount may have thousands commas, and is sent without them.
  - New cases in `field-values.spec.ts` and `answer-form.spec.ts`.
- To verify: run the frontend tests, lint and format (`./check.sh`), rebuild the frontend image, then type "18,000", "₪18,000" and "6%" in My Details and "1,350" on `/rep`.

## Fix plan

| Finding | Layer | Effort | Test after the fix |
|---|---|---|---|
| F-2 model code with a name | Code | small | scripted test in `tests/agent/test_tools.py` |
| F-1 retry with the Latin name | Prompt (+ optional code alias) | small | live rerun of E-001 with the Hebrew name |
| F-3 confirmation and catalog first | Prompt + code guard | medium | scripted test for the guard, live rerun of E-006 |
| F-4 combined form | Prompt | small | live rerun of E-001 |
| F-6 units and commas in the forms | Code (frontend) | done, unverified | `./check.sh`, then by hand in the browser |
| F-5 latency | Prompt, then model choice | open-ended | live rerun of all, check `agent_turn` durations |

After any prompt change, run `./check.sh`, because `tests/agent` checks the instructions with scripted models. Then rerun the live cases above and update this file.
