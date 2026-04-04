# ManyChat Developer Reference

## 1. Trigger Keywords

Each flow needs one of these as the trigger keyword:

| Code | Flow Name | Notes |
|---|---|---|
| HEAL | 1:1 Session | High-intent |
| UNTANGLE | 1:1 Session | Alternate for HEAL, high-intent |
| STEADY | 1:1 Session | Alternate for HEAL, high-intent |
| MALIBURETREAT | Healing with Horses Retreat | High-intent |
| MALIBU RETREAT | Healing with Horses Retreat | Alternate (with space), high-intent |
| UNLEARN | Mother Hunger Course | |
| WORTHY | Emotional Starter Kit | Free |
| GRIEFRELIEF | Grief Relief Video Series | |
| GRIEFTOOLS | Grief Relief Video Series | Alternate |
| TOOLS | 101 Tools | Digital product |
| EQUINE | Equine Digital Product | |
| HORSEHEALING | Equine Digital Product | Alternate |
| MOM | Community Circle | Free |
| COMMUNITYCALL | Motherless Daughters Group | |
| EMDR | EMDR Therapy | High-intent |
| TAPPERS | Dharma Dr. | |

---

## 2. Webhook Setup

In each ManyChat flow, add an **External Request (POST)** action:

**URL:** `https://<YOUR_DOMAIN>/api/manychat/webhook`

**Method:** POST

**JSON Body:**

```json
{
  "id": "{{subscriber_id}}",
  "first_name": "{{first_name}}",
  "last_name": "{{last_name}}",
  "full_name": "{{full_name}}",
  "email": "{{email}}",
  "phone": "{{phone}}",
  "ig_username": "{{ig_username}}",
  "profile_pic": "{{profile_pic}}",
  "gender": "{{gender}}",
  "keyword": "HEAL",
  "source": "instagram",
  "tags": [],
  "custom_fields": {}
}
```

Replace `"HEAL"` with the keyword for that specific flow.

**Health check:** `GET /api/manychat/webhook` returns `{"status": "ok"}`

---

## 3. Lead Scoring (Custom Fields in ManyChat)

The webhook auto-calculates these:

**interest_level / heat_score:**

| Level | Score | Rule |
|---|---|---|
| vip | 90 | High-intent keyword + (3+ conversations OR 3+ unique triggers) |
| hot | 70 | High-intent keyword OR (3+ triggers + 2+ conversations) |
| warm | 50 | 2+ triggers OR 2+ conversations |
| cold | 25 | 1 trigger |
| new | 5 | No triggers yet |

High-intent keywords: `MALIBURETREAT`, `MALIBU RETREAT`, `HEAL`, `UNTANGLE`, `STEADY`, `EMDR`

**funnel_stage:**

| Stage | Rule |
|---|---|
| booked | Tagged "booked", "purchased", or "client" |
| conversation | 3+ conversations |
| multi_trigger | 3+ triggers fired |
| engaged | At least 1 trigger or conversation |
| subscriber | Default, no activity |

---

## 4. CRM Data Fields

### manychat_subscribers (main CRM record — 28 fields)

| Field | Type | Default | What It Stores |
|---|---|---|---|
| id | BIGSERIAL | auto | Internal database ID |
| mc_id | TEXT (unique) | required | ManyChat subscriber ID |
| first_name | TEXT | '' | First name |
| last_name | TEXT | '' | Last name |
| full_name | TEXT | '' | Full name |
| email | TEXT | '' | Email address |
| phone | TEXT | '' | Phone number |
| ig_username | TEXT | '' | Instagram handle |
| profile_pic | TEXT | '' | Profile picture URL |
| gender | TEXT | '' | Gender |
| locale | TEXT | '' | Language/locale |
| subscribed_at | TIMESTAMP | null | When they first subscribed |
| last_interaction | TIMESTAMP | null | Last interaction with any flow |
| last_seen | TIMESTAMP | null | Last time seen active |
| ig_last_interaction | TIMESTAMP | null | Last Instagram interaction |
| opted_in_ig | BOOLEAN | false | Opted in to IG messages |
| opted_in_email | BOOLEAN | false | Opted in to email |
| tags | JSON array | [] | All ManyChat tags on this contact |
| custom_fields | JSON object | {} | All ManyChat custom fields |
| trigger_count | INT | 0 | How many keywords they've triggered total |
| conversation_count | INT | 0 | How many DM conversations they've had |
| interest_level | TEXT | 'new' | Auto-scored: new / cold / warm / hot / vip |
| heat_score | INT | 0 | Auto-scored: 5 / 25 / 50 / 70 / 90 |
| funnel_stage | TEXT | 'subscriber' | Auto-scored: subscriber / engaged / multi_trigger / conversation / booked |
| flodesk_synced | BOOLEAN | false | Whether synced to Flodesk email |
| do_not_contact | BOOLEAN | false | Do-not-contact flag |
| synced_at | TIMESTAMP | now() | Last time data was synced from ManyChat |
| created_at | TIMESTAMP | now() | When this record was created |
| updated_at | TIMESTAMP | now() | When this record was last updated |

### manychat_triggers (9 fields)

| Field | Type | Default | What It Stores |
|---|---|---|---|
| id | BIGSERIAL | auto | Internal ID |
| keyword | TEXT (unique) | required | The trigger keyword (e.g. HEAL) |
| label | TEXT | required | Display name (e.g. "1:1 Session") |
| description | TEXT | '' | What this trigger is for |
| product_url | TEXT | '' | Link to the product/service |
| is_active | BOOLEAN | true | Whether this trigger is currently active |
| sort_order | INT | 0 | Display order |
| created_at | TIMESTAMP | now() | When created |
| updated_at | TIMESTAMP | now() | When last updated |

### subscriber_triggers — logs every keyword fired (7 fields)

| Field | Type | Default | What It Stores |
|---|---|---|---|
| id | BIGSERIAL | auto | Internal ID |
| mc_id | TEXT | required | ManyChat subscriber ID |
| keyword | TEXT | required | Which keyword they triggered |
| source | TEXT | 'instagram' | Where it came from (instagram, etc.) |
| fired_at | TIMESTAMP | now() | When they triggered it |
| post_id | TEXT | '' | Which post triggered it (if from comments) |
| created_at | TIMESTAMP | now() | When logged |

### subscriber_conversations — DM history (8 fields)

| Field | Type | Default | What It Stores |
|---|---|---|---|
| id | BIGSERIAL | auto | Internal ID |
| mc_id | TEXT | required | ManyChat subscriber ID |
| direction | TEXT | required | "inbound" or "outbound" |
| message_preview | TEXT | '' | First part of the message |
| flow_name | TEXT | '' | Which ManyChat flow sent it |
| channel | TEXT | 'instagram' | Channel (instagram, etc.) |
| sent_at | TIMESTAMP | now() | When message was sent |
| created_at | TIMESTAMP | now() | When logged |

### webhook_events — health monitoring (11 fields)

| Field | Type | Default | What It Stores |
|---|---|---|---|
| id | BIGSERIAL | auto | Internal ID |
| event_type | TEXT | 'trigger' | Type of event |
| source | TEXT | 'manychat' | Where it came from |
| mc_id | TEXT | '' | ManyChat subscriber ID |
| keyword | TEXT | '' | Trigger keyword |
| subscriber_name | TEXT | '' | Name of subscriber |
| status | TEXT | 'success' | success or error |
| error_message | TEXT | '' | Error details if failed |
| payload_preview | TEXT | '' | First 500 chars of the payload |
| processing_ms | INT | 0 | How long it took to process |
| created_at | TIMESTAMP | now() | When event occurred |

---

## 5. API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | /api/manychat/webhook | Receive trigger data from ManyChat flows |
| GET | /api/manychat/webhook | Health check |
| GET | /api/manychat/triggers | List all triggers |
| POST | /api/manychat/triggers | Create trigger |
| PUT | /api/manychat/triggers/{id} | Update trigger |
| DELETE | /api/manychat/triggers/{id} | Delete trigger |
| PATCH | /api/manychat/triggers/{id}/toggle | Toggle active/inactive |
| POST | /api/manychat/sync | Sync subscribers from ManyChat API |
| GET | /api/manychat/subscribers | List subscribers |
| GET | /api/manychat/subscribers/{mc_id} | Get single subscriber |
| GET | /api/webhooks/dashboard | Webhook health stats |
| GET | /api/webhooks/events | List webhook events |
