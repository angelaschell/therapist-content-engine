# ManyChat Webhook Schema — Therapist Content Engine

## Webhook Endpoint

```
POST /api/manychat/webhook
```

---

## Primary Table: `manychat_subscribers`

This is the main subscriber/lead table. The ManyChat flow should POST a JSON body with fields matching these exact column names.

| Column Name | Data Type | Notes |
|---|---|---|
| id | BIGSERIAL | Auto-generated primary key — do NOT send |
| mc_id | TEXT NOT NULL UNIQUE | **REQUIRED** — ManyChat subscriber ID, links everything |
| first_name | TEXT | |
| last_name | TEXT | |
| full_name | TEXT | |
| email | TEXT | |
| phone | TEXT | |
| ig_username | TEXT | Instagram handle |
| profile_pic | TEXT | URL to profile picture |
| gender | TEXT | |
| locale | TEXT | |
| subscribed_at | TIMESTAMPTZ | When they first subscribed |
| last_interaction | TIMESTAMPTZ | Last interaction timestamp |
| last_seen | TIMESTAMPTZ | |
| ig_last_interaction | TIMESTAMPTZ | Last Instagram interaction |
| opted_in_ig | BOOLEAN | Instagram opt-in status |
| opted_in_email | BOOLEAN | Email opt-in status |
| tags | JSONB | Array format, e.g. `["tag1", "tag2"]` |
| custom_fields | JSONB | Object format, e.g. `{"field_name": "value"}` |
| trigger_count | INT | Number of keyword triggers fired |
| conversation_count | INT | Number of conversations |
| interest_level | TEXT | Values: `new`, `warm`, `hot`, etc. |
| heat_score | INT | 0–100 lead score |
| funnel_stage | TEXT | Values: `subscriber`, `lead`, `customer`, etc. |
| flodesk_synced | BOOLEAN | Whether synced to Flodesk |
| do_not_contact | BOOLEAN | DNC flag |
| synced_at | TIMESTAMPTZ | Auto-set |
| created_at | TIMESTAMPTZ | Auto-set |
| updated_at | TIMESTAMPTZ | Auto-set |

---

## Trigger Tracking Table: `subscriber_triggers`

Logs each keyword trigger a subscriber fires. One row per trigger event.

| Column Name | Data Type | Notes |
|---|---|---|
| id | BIGSERIAL | Auto-generated primary key |
| mc_id | TEXT | **REQUIRED** — links to manychat_subscribers.mc_id |
| keyword | TEXT | The trigger keyword (e.g. `WORTHY`, `HEAL`, `UNLEARN`) |
| source | TEXT | Default: `instagram` |
| fired_at | TIMESTAMPTZ | When the trigger was fired |
| post_id | TEXT | Instagram post ID if applicable |
| created_at | TIMESTAMPTZ | Auto-set |

---

## Keyword Definitions Table: `manychat_triggers`

Defines all available trigger keywords. These are pre-populated — the Fiverr developer should reference these when building flows.

| Column Name | Data Type | Notes |
|---|---|---|
| id | BIGSERIAL | Auto-generated primary key |
| keyword | TEXT NOT NULL UNIQUE | The trigger word |
| label | TEXT | Human-readable product name |
| description | TEXT | What the trigger is for |
| product_url | TEXT | Link to the product |
| is_active | BOOLEAN | Whether this trigger is active |
| sort_order | INT | Display order |
| created_at | TIMESTAMPTZ | Auto-set |
| updated_at | TIMESTAMPTZ | Auto-set |

### Current Keywords

| Keyword | Label |
|---|---|
| HEAL | 1:1 Session |
| UNTANGLE | 1:1 Session (alternate) |
| STEADY | 1:1 Session (alternate) |
| MALIBURETREAT | Healing with Horses Retreat |
| MALIBU RETREAT | Healing with Horses Retreat (alternate) |
| UNLEARN | Mother Hunger Course |
| WORTHY | Emotional Starter Kit (free) |
| GRIEFRELIEF | Grief Relief Video Series |
| GRIEFTOOLS | Grief Relief Video Series (alternate) |
| TOOLS | 101 Tools |
| EQUINE | Equine Digital Product |
| HORSEHEALING | Equine Digital Product (alternate) |
| MOM | Community Circle (free) |
| COMMUNITYCALL | Motherless Daughters Group |
| EMDR | EMDR Therapy |
| TAPPERS | Dharma Dr. |

---

## Conversation Log Table: `subscriber_conversations`

| Column Name | Data Type | Notes |
|---|---|---|
| id | BIGSERIAL | Auto-generated primary key |
| mc_id | TEXT | **REQUIRED** — links to manychat_subscribers.mc_id |
| direction | TEXT | `inbound` or `outbound` |
| message_preview | TEXT | First ~200 chars of message |
| flow_name | TEXT | Which ManyChat flow sent/received it |
| channel | TEXT | Default: `instagram` |
| sent_at | TIMESTAMPTZ | When the message was sent |
| created_at | TIMESTAMPTZ | Auto-set |

---

## Example Webhook JSON Body

```json
{
  "mc_id": "123456789",
  "first_name": "Jane",
  "last_name": "Doe",
  "full_name": "Jane Doe",
  "email": "jane@example.com",
  "phone": "+15551234567",
  "ig_username": "janedoe",
  "profile_pic": "https://...",
  "gender": "female",
  "locale": "en_US",
  "opted_in_ig": true,
  "opted_in_email": true,
  "tags": ["WORTHY", "warm_lead"],
  "custom_fields": {"source": "instagram_story"},
  "keyword": "WORTHY"
}
```

The `keyword` field in the webhook body will automatically log a row in `subscriber_triggers`.

---

## Important Notes

- **`mc_id` is the primary key** that links all tables together — every webhook MUST include it
- All TIMESTAMPTZ fields should be sent as ISO 8601 strings (e.g. `2026-04-04T12:00:00Z`)
- `tags` is a JSON array, `custom_fields` is a JSON object
- Fields with auto-set defaults (created_at, updated_at, synced_at) do not need to be sent
- The `id` column is auto-generated — never send it in the webhook body
