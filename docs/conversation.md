# Talk modes: chatting and brainstorming

Most of what Nova does is aimed at finishing a job. Talk modes are for when you just want to talk:
think out loud, kick ideas around, or have a fun conversation.

| Say | What happens |
|---|---|
| "let's chat", "can we just talk for a bit", "talk to me" | **Just talking** |
| "let's brainstorm about a name for my podcast", "brainstorm mode" | **Brainstorming**, with a topic if you give one |
| "what have we got so far" | brainstorm recap, and the mode carries on |
| "wrap it up" | recap, saved, then the mode ends |
| "back to work", "that's all for now" | the mode ends (a brainstorm with ideas in it is recapped and saved first) |

The canvas shows a chip next to the status while a mode is on. After 15 quiet minutes the
mode ends by itself.

## What changes while a mode is on

- **No wake word after a reply.** The microphone stays open for 20 seconds after every reply,
  not only after questions, so you answer the way you'd answer a person.
- **What you say is talk, never an action.** Automations, the loose matcher and the classifier are
  skipped. Only stop, cancel, quit and the mode's own phrases are commands. (Outside a mode,
  "gulzar ali" said on its own was once routed to a phone call.)
- **The free fast model, with no tools.** Each turn costs no subscription and sends no tool schemas,
  so it is the cheapest turn Nova has, and fast enough for back-and-forth. It runs on a copy of
  the model that keeps the conversation so far, so a task running in the background is untouched.
- **A voice with feeling.** The model may use delivery tags such as `[chuckles]`, `[laughs softly]`, `[sighs]`,
  `[excited]`, `[whispering]`, or any short description a voice actor would understand
  (Fish S2 takes free-form tags). They're performed, never shown, and dropped if the local voice
  has to take over.

**Chatting** is short turns (one to three sentences), opinions, a question back, humour.
**Brainstorming** builds on your ideas ("yes, and…"), adds two or three of its own each turn with
at least one unexpected one, gives each idea a memorable name, points out holes, and ends turns with
one sharp question.

## The delivery rule (laughing, smiling, stressing a word)

`assistant/persona/delivery.py` holds one structured rule, used by every brain that writes a spoken reply:

- **Conversation modes get the full rule:** the format (a tag right before the words it colours, lasting until the next one),
  a table from the moment to the tag (funny → `[chuckles]`, greeting or good news → `[warmly]`,
  the key fact → `[emphasis]`, a great idea → `[excited]`, bad news → `[gently]`, weighing up →
  `[thoughtful]`, irony → `[sighs]`/`[dryly]`, an aside → `[whispering]`), how many (most replies one,
  never more than two), a never-do list, and examples.
- **Every other reply gets one sentence of it** (about 65 tokens, since it's resent on every free-model step):
  at most one tag, for warmth, the key fact, or bad news.
- **A safety net in code:** `*laughs*`, `(smiles)`, "haha" and emoji are turned into tags before speaking, and
  a laugh left at the very end moves to the start of the last sentence, so it's still performed.

To change how expressive Nova is, edit `MOMENTS`, `COUNT` or `TASK_GUIDE` in that file.

## Recaps

A recap is Markdown: ideas grouped under headings, the strongest marked, and open questions at the end.
It's saved to `Documents\Nova Brainstorms\<date> <topic>.md` and shown in the reader window, and
Nova speaks a one-line gist.

## The voice

Nova always uses one fixed voice: `[voice] fish_voice` is EDITH's ID. Left empty, Fish chooses a voice per
request, and because replies are fetched a sentence at a time, Nova changed voice mid-reply and with
every expression. Emotion comes from the delivery tags on that one voice. `fish_temperature` (0.6) and
`fish_top_p` (0.7) set how much each line varies: lower keeps the timbre steadier, higher is livelier.

## Code

`assistant/conversation.py` holds the modes, their instructions and recap saving. The routing is in
`controller.handle`, `_start_talk`, `_converse` and `_recap`. The phrases are in `intents.py`,
`ChatAgent.talk` in `agents/chat_api.py` swaps the prompt and drops the tools, and the canvas chip
uses the `talk` state slice. Tests: `tests/test_talk_modes.py`.
