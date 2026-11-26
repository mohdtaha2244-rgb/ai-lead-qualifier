# AI Lead Qualification System (demo)

A lead writes on WhatsApp or fills a property-portal form. Within seconds the system replies, answers their question from the client's own listings, and collects what the sales team needs: what they want, where, budget, when, and how they will pay. Then fixed rules score the lead, the right agent gets an alert, the lead picks a viewing time, and follow-ups go out by themselves.

**Live demo:** https://ai-lead-qualifier-8mhljqsunkpknd5wozd5jp.streamlit.app/

Built by Mohammed Taha with Claude Code. The businesses (Demo Realty Dubai, Demo Motors Dubai) and the sample leads are fictional.

## The client problem it solves

A Dubai brokerage gets leads from portals, Instagram and WhatsApp at all hours. Typical pain:

- **Slow first reply.** Agents answer hours later, and by then the buyer has called another broker.
- **Agents waste time on weak leads.** They spend their day on WhatsApp admin instead of viewings.
- **Follow-ups are forgotten.** A lead who goes quiet is never contacted again.
- **The manager has no view.** Nobody knows how many leads came in, which are hot, or who is handling them.

## What the system does

| Step | What happens | Who decides |
|---|---|---|
| 1. Message arrives | From WhatsApp, a portal form or the website. Same phone number = same lead, no duplicates. | Code |
| 2. Quick checks | `STOP` opts out. "Talk to an agent" hands over. "1", "2" or "3" books a viewing time. | Code (no AI needed) |
| 3. Reply | AI pulls the details out of the message and writes a short reply in English or Arabic, only from the client's facts and listings. | AI (Gemini) |
| 4. Score | 0 to 100, with a written reason for every point. HOT, WARM, COLD or NOT A LEAD. | Fixed rules |
| 5. Act | HOT: agent picked by area (VIP budget goes to the senior agent), WhatsApp alert, 3 free viewing times. WARM: the agent calls within a day. COLD: nurture. | Code |
| 6. Follow up | 6 messages over 14 days if the lead goes quiet, then stop. Reminder the day before a viewing. | Code |
| 7. Record | Every action goes in an audit log. Pipeline, alerts and a weekly manager summary. CSV export for HubSpot or Zoho. | Code |

## Design decisions

1. **The AI only does language. Rules make the decisions.** The AI reads messages and writes replies. Scoring, agent choice and booking are plain code, so they are the same every time, explainable to a sales manager, and testable.
2. **One engine, many clients.** Everything about a business (questions, scoring rules, agents, areas, follow-up texts) lives in one JSON file in `clients/`. The car dealer runs on the same code as the brokerage. A new client means a new rules file, not new code.
3. **Fail safely, not silently.** If the AI is down, slow or out of quota, the lead still gets a polite reply and the chat goes to a person.
4. **Respect the customer.** The first message says it is an AI assistant and how to stop. `STOP` ends all automatic messages. A maximum of 6 follow-ups.
5. **Never guess.** If the AI returns an answer that isn't allowed (for example a timeline of "maybe someday"), it is dropped, and the question is asked again.

## Files

| File | Job |
|---|---|
| `engine.py` | The brain: runs every message through the steps above |
| `ai.py` | The only file that talks to the AI (easy to switch providers) |
| `scoring.py` | Score and tier from the client's rules |
| `routing.py` | Which agent gets the lead |
| `followup.py` | Follow-ups, viewing reminders, free viewing times |
| `checks.py` | Language, UAE phone numbers, STOP, "talk to a person", slot choice |
| `db.py` | SQLite database: leads, messages, audit log |
| `clients/*.json` | One rules file per client business |
| `app.py` | The demo screens: chat, pipeline, manager report, ROI calculator, AI quality test, rules |
| `tests/` | 71 unit tests (no AI, no internet) |
| `evals/` | AI quality test: 15 real chats run against the live AI |

## Tests

`python -m pytest` runs 71 unit tests in about a second. No AI and no internet needed: the engine tests use a fake AI, so they check the system's own decisions every time, for free.

They cover money parsing ("2.5M", "٢ مليون"), scoring for buyers, renters and car buyers, routing by area and VIP budget, UAE phone numbers, STOP, handover, the 6-follow-up limit, viewing reminders, an AI failure, duplicate leads, two leads trying to book the same time, and the AI fallback rules (a switched-off model, a model out of quota, a slow model).

### AI quality test (live)

Unit tests check our code. This checks the AI itself: 15 real customer chats go through the real system and the real AI (Gemini), and plain code checks each result: the details found, the tier, the agent, the reply language, no made-up prices, no leaked rules, and no question asked twice.

The chats: a cash buyer who says everything at once, a Gulf Arabic renter, 6 short answers in a row, a price question, a question the facts can't answer, a prompt-injection attack, a discount request, a visa question, an angry customer, a job seeker, a low-budget browser, an off-topic question, an AED 35M buyer, a customer who changes their budget, and a car buyer.

**Saved full run: 15 / 15 passed** (26 Nov 2026, `evals/saved_report.json`, also shown in the app). On the free AI plan, some later runs had 1 or 2 failures when Google returned "high demand" (503) or timeout (504) errors, and one run near the end hit the free daily quota after many runs. In every one of those cases the customer still got a polite reply and the chat went to a person.

**Real bugs the tests caught (all fixed, each with a new test):**

1. **A perfect buyer scored 91, not 100.** Two "when are you moving" rules (within 3 months, within 6 months) were both counted as possible points, but only one can ever be true. Fix: rules about the same detail count once.
2. **A job seeker was handed to an agent.** "Are you hiring real estate agents?" contains the word "agent", which was on the "wants a person" list. Fix: full phrases such as "talk to an agent", plus a test for this exact message.
3. **Google switched off an old AI model** (404 "no longer available to new users"). The system kept trying it first. Found by the first live run. Fix: a switched-off model is skipped forever, and every model's error is saved in the audit log.
4. **The backup models were never reached.** When the newest models were out of quota, the list of models to try was full of other main models, so the "lite" backups were not tried. Fix: always 2 main models plus 3 backups.
5. **A slow model used the whole time limit.** After a timeout, the same model was tried again. Fix: a slow model rests and the next one answers. If no answer comes within 25 seconds, a person takes over.
6. **A job seeker went to an agent** because the AI flagged "needs a person". Fix: when the rules say "not a lead", it never takes an agent's time, whatever the AI says.

## What a real client launch would add

- WhatsApp Business Cloud API webhook calling `handle_message()` (the engine is already channel-agnostic)
- CRM sync (HubSpot or Zoho API) and Google or Outlook calendar for agents' real free times
- A scheduler running `run_follow_ups()` every 5 minutes, and PostgreSQL instead of SQLite
- Opt-in records and data handling in line with the UAE data protection law (PDPL), agreed with the client

## Run it

```
pip install -r requirements.txt pytest
python -m pytest
python -m streamlit run app.py
```

Add `GEMINI_API_KEY` to `.streamlit/secrets.toml` (never commit it). Without a key the app runs in safe mode: every message goes to a person.
