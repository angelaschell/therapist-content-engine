# ManyChat Flow Scripts — Angela Voice

Every flow below matches the brand rules in `main.py` (ANGELA_SYSTEM) and the trigger descriptions in `manychat_backend.py` (SEED_TRIGGERS).

**How to use this file:**
1. Open Chrome side-by-side: this file on the left, ManyChat on the right.
2. For each flow, paste the blocks in order.
3. After the opening delivery, every flow ends with a **User Input → External Request (Claude) → Condition** pattern so the new `/api/manychat/claude-reply` endpoint routes them automatically.
4. **Never** type an em dash. Never say "healing era," "holding space," "do the work," "trauma dump," "you are not broken." No urgency, no scarcity, no outcome promises.

**Placeholders to find-and-replace before publishing:**
- `{{STARTER_KIT_URL}}`
- `{{BOOKING_URL_1ON1}}`
- `{{MALIBU_RETREAT_URL}}`
- `{{MOTHER_HUNGER_URL}}`
- `{{GRIEF_RELIEF_URL}}`
- `{{TOOLS_101_URL}}`
- `{{EQUINE_GUIDE_URL}}`
- `{{THURSDAY_GROUP_URL}}`
- `{{EMDR_INFO_URL}}`
- `{{DHARMA_DR_URL}}`
- `{{RENDER_URL}}` — your deployed backend (e.g. `https://therapist-content-engine.onrender.com`)

**Custom fields required** (Settings → Fields):
`claude_category`, `claude_product_fit`, `claude_heat_score` (number), `claude_last_reply`, `claude_reasoning`, `claude_suggested_trigger`

**Tags required** (Settings → Tags):
`lead:cold`, `lead:warming`, `lead:warm`, `lead:hot`, `fit:starter`, `fit:1on1`, `fit:retreat`, `fit:course`, `fit:video`, `fit:emdr`, `fit:community`, `synced:flodesk`, `do-not-contact`

---

## The reusable Claude routing block (used at the end of every flow)

Every flow ends with this pattern. Set it up once as a **Draft block** in ManyChat, then duplicate into each flow so you only write it once.

### Step A. User Input
- **Question:** `tell me where you are with this right now. one sentence is plenty.`
- **Save answer to:** `last_user_reply` (create as Text custom field if it doesn't exist)
- **Skip button text:** `not yet`

### Step B. External Request (this is where Claude comes in)
- **Method:** POST
- **URL:** `{{RENDER_URL}}/api/manychat/claude-reply`
- **Headers:**
  - `Content-Type: application/json`
  - `X-ManyChat-Secret: {{MANYCHAT_WEBHOOK_SECRET value}}`
- **Body (JSON):**
  ```json
  {
    "contact_id": "{{contact_id}}",
    "user_message": "{{last_user_reply}}",
    "keyword": "<TRIGGER KEYWORD FOR THIS FLOW>",
    "first_name": "{{first_name}}"
  }
  ```
- **Response mapping** (ManyChat → custom field):
  - `content.messages.0.text` → `claude_last_reply`
  - `content.actions` already contains `set_field_value` actions; enable "Apply actions automatically" so ManyChat writes `claude_category`, `claude_product_fit`, `claude_heat_score`, `claude_reasoning`, `claude_suggested_trigger` for you.

### Step C. Send Message (Claude's reply)
- **Text:** `{{claude_last_reply}}`
- **Delay:** 2 seconds before.

### Step D. Condition — route by `claude_category`
- **Branch 1** → `claude_category` equals `ready_to_buy` **OR** `hot`
  - Tag: `lead:hot`
  - Send Angela an internal notification (Admin-only message node)
  - Send: `one more thing. I want to send you the next step personally. give me until tomorrow morning.`
  - Exit.
- **Branch 2** → `claude_category` equals `warm`
  - Sub-condition on `claude_product_fit`:
    - `malibu_retreat` → route into the MALIBURETREAT flow
    - `1on1` → route into the HEAL flow
    - `mother_hunger` → route into the UNLEARN flow
    - `emdr` → route into the EMDR flow
    - `community` → route into the MOM flow
    - `grief_relief` → route into the GRIEFRELIEF flow
    - anything else → stay, send: `I want to send you the right next thing. sit with what came up. I'll be in touch.` Exit.
- **Branch 3** → `claude_category` equals `warming`
  - Tag: `lead:warming`
  - Trigger "3-day nurture sequence" (set this up once as its own flow).
- **Branch 4** → `claude_category` equals `cold` (Otherwise)
  - Tag: `lead:cold`
  - Send: `I'll keep writing. come back when something hits. I'm here.`
  - Exit.

---

## 1. WORTHY → Emotional Starter Kit

**Trigger description (from code):** `my free Emotional Starter Kit`

### Opening (sent immediately)
```
you asked for it.

the Emotional Starter Kit is yours.
```

### Message 2 (delay: 2 seconds)
```
{{STARTER_KIT_URL}}
```

### Message 3 (delay: 4 seconds)
```
open it when you have ten minutes and a door you can close.

not in the carpool line. not on a lunch break.

it's not long. it's just honest.
```

### Message 4 (delay: 3 seconds)
```
I made this for the woman who looks fine on paper and doesn't feel fine in her body.

if that's you, you're in the right inbox.
```

### Then: Claude routing block (keyword value = `WORTHY`)

---

## 2. HEAL / UNTANGLE / STEADY → 1:1 Therapy Session

**Trigger description (from code):** `the link to book a 1:1 therapy session`

Use the same flow for all three keywords.

### Opening
```
here's the link to book a 1:1 session with me.

{{BOOKING_URL_1ON1}}
```

### Message 2 (delay: 3 seconds)
```
a few things before you book.

I work with grief that doesn't look like grief. the kind that shows up as insomnia, irritation, the inability to sit still.

I work with attachment wounds that started before you had words for them.

I work with women who are high functioning in every part of their life except the part where they're alone in a room with themselves.
```

### Message 3 (delay: 4 seconds)
```
if any of that sounds like why you're here, book a first session and we'll start there.

if you want to tell me a little about what brought you before you book, reply and I'll read it myself.
```

### Then: Claude routing block (keyword value = `HEAL`)

---

## 3. MALIBURETREAT / MALIBU RETREAT → Healing with Horses Retreat

**Trigger description (from code):** `details about the Healing with Horses Somatic Grief Retreat in Malibu`

### Opening
```
Healing with Horses.

the somatic grief retreat at Shakti Ranch in Malibu.

full details here: {{MALIBU_RETREAT_URL}}
```

### Message 2 (delay: 3 seconds)
```
this is not a conference. it is not a group workshop where you talk about your feelings for three days.

it is horses. it is land. it is your nervous system finally getting to exhale in a place that was built for that exact thing.
```

### Message 3 (delay: 4 seconds)
```
the women who come tend to share one sentence: I didn't know I was allowed to rest like this.

that sentence is the whole point.
```

### Message 4 (delay: 3 seconds)
```
spaces are small on purpose. if the dates are calling you, read the page and then come back and tell me what came up when you read it.
```

### Then: Claude routing block (keyword value = `MALIBURETREAT`)

---

## 4. UNLEARN → Mother Hunger Course

**Trigger description (from code):** `info about the Mother Hunger Course`

### Opening
```
the Mother Hunger course.

the framework is Kelly McDaniel's work. I teach it through the lens of my own practice as a grief and trauma therapist.

{{MOTHER_HUNGER_URL}}
```

### Message 2 (delay: 3 seconds)
```
three essentials of attachment: nurturance, protection, guidance.

if you didn't get one of those from your mother, you spend the rest of your life performing adulthood around the hole where it should have been.

most women walk around not knowing that is what they are doing.
```

### Message 3 (delay: 4 seconds)
```
the course is slow on purpose. you watch one module, you sit with it, you come back.

it is not a cleanse. it is a reckoning.
```

### Message 4 (delay: 3 seconds)
```
if the word mother made your stomach drop when you read this, you are the woman I made this for.
```

### Then: Claude routing block (keyword value = `UNLEARN`)

---

## 5. GRIEFRELIEF / GRIEFTOOLS → Grief Relief Video Series

**Trigger description (from code):** `the Grief Relief Video Series`

### Opening
```
the Grief Relief Video Series.

{{GRIEF_RELIEF_URL}}
```

### Message 2 (delay: 3 seconds)
```
short videos. one concept each. no fluff, no intro music, no performance.

I made them for the nights you cannot sleep and the mornings you cannot get up.
```

### Message 3 (delay: 4 seconds)
```
you do not have to watch them in order. you do not have to finish them.

take the one you need tonight. come back to the rest when you're ready.
```

### Then: Claude routing block (keyword value = `GRIEFRELIEF`)

---

## 6. TOOLS → 101 Tools Resource

**Trigger description (from code):** `my 101 Tools resource`

### Opening
```
101 Tools.

{{TOOLS_101_URL}}
```

### Message 2 (delay: 3 seconds)
```
grounding. somatic. nervous system. attachment. grief. boundaries. sleep.

101 of the tools I actually use in sessions, written down so you can reach for them when I am not in the room.
```

### Message 3 (delay: 4 seconds)
```
you do not have to use all of them. pick one this week. see what happens in your body.

the goal is not a full toolbox. the goal is knowing where to reach when the wave hits.
```

### Then: Claude routing block (keyword value = `TOOLS`)

---

## 7. EQUINE / HORSEHEALING → Equine Therapy Guide

**Trigger description (from code):** `my Equine Therapy digital guide`

### Opening
```
the Equine Therapy guide.

{{EQUINE_GUIDE_URL}}
```

### Message 2 (delay: 3 seconds)
```
horses do not lie. they do not negotiate. they do not care what you do for a living.

they show you exactly what your nervous system is doing the moment you walk up to them.

that is why it works.
```

### Message 3 (delay: 4 seconds)
```
the guide walks you through what equine therapy actually is, who it is for, and what a session at Shakti Ranch looks like.

read it. see if something in you pulls toward the gate.
```

### Then: Claude routing block (keyword value = `EQUINE`)

---

## 8. MOM / COMMUNITYCALL → Motherless Daughters Thursday Group

**Trigger description (from code):** `the link to join the Grief, Trauma and Your Mama community` / `the link to the Motherless Daughters Thursday group`

### Opening
```
Thursdays. Motherless Daughters. together.

{{THURSDAY_GROUP_URL}}
```

### Message 2 (delay: 3 seconds)
```
we know the way the word mother has a different weight for us.

we know the days on the calendar most people walk past without flinching.

we know what it is to parent ourselves through adulthood and call it independence.
```

### Message 3 (delay: 4 seconds)
```
the group is women who get it without you having to translate.

no homework. no reading. no performance.

you show up. you stay on camera or you don't. you leave lighter than you came.
```

### Message 4 (delay: 3 seconds)
```
if you know, you know. the link is above.
```

### Then: Claude routing block (keyword value = `MOM`)

---

## 9. EMDR → EMDR Therapy Sessions

**Trigger description (from code):** `info about EMDR therapy sessions`

### Opening
```
EMDR.

here is how I work with it in my practice: {{EMDR_INFO_URL}}
```

### Message 2 (delay: 3 seconds)
```
EMDR is not hypnosis. it is not tapping. it is not a gimmick.

it is a protocol that lets your brain process memory the way it was supposed to process it the first time, before it got stuck.
```

### Message 3 (delay: 4 seconds)
```
I use it with women whose bodies are still living in a moment their minds have already explained away.

the memory loses its charge. you don't lose the memory. you just stop bracing around it.
```

### Message 4 (delay: 3 seconds)
```
if you want to book an EMDR intake, start here: {{BOOKING_URL_1ON1}}

or reply and tell me what you are hoping to move. I read these myself.
```

### Then: Claude routing block (keyword value = `EMDR`)

---

## 10. TAPPERS → Dharma Dr. Resource

**Trigger description (from code):** `info about the Dharma Dr. resource`

### Opening
```
Dharma Dr.

{{DHARMA_DR_URL}}
```

### Message 2 (delay: 3 seconds)
```
this is the resource I point people to when they want something to work with between sessions. somatic. practical. doable on a Tuesday night in your own living room.
```

### Message 3 (delay: 4 seconds)
```
open the link when you have a quiet twenty minutes. let it meet you where you are.
```

### Then: Claude routing block (keyword value = `TAPPERS`)

---

## Global flow rules (apply to EVERY flow above)

- **Opening message timing:** send immediately, no pre-delay.
- **Between-message delays:** 2 to 5 seconds only. Never instant, never longer than 5.
- **Personalization:** use `{{first_name | fallback: friend}}` anywhere you address the person directly, but not in every single message — it reads robotic.
- **Exit condition on every flow:** after the Claude routing block runs, the flow ends. No loops.
- **Dead-ends are banned.** Every path either hands off to Angela, routes to another flow, or drops into Flodesk nurture.
- **Comment auto-reply:** if the trigger fires from an Instagram post comment, the DM version + the public comment reply must both go out. Public comment reply template:
  ```
  just sent you the link {{first_name | fallback: love}}. check your DMs.
  ```

---

## After you finish pasting

1. Test ONE flow end-to-end before touching the others. Pick WORTHY.
2. DM yourself the keyword from your personal Instagram.
3. Confirm: the delivery message arrives, the User Input captures your reply, Claude's reply comes back in your brand voice, the `claude_category` field populates, the Condition node branches correctly, and the tag shows up on your contact.
4. Check the Lead CRM at `{{RENDER_URL}}` — the contact should appear with the new analysis blob filled in.
5. Only after WORTHY works, replicate into the other nine flows.
