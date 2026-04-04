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

## 4. Database Tables

### manychat_subscribers (main CRM record)

```sql
CREATE TABLE IF NOT EXISTS manychat_subscribers (
  id BIGSERIAL PRIMARY KEY,
  mc_id TEXT NOT NULL UNIQUE,
  first_name TEXT DEFAULT '',
  last_name TEXT DEFAULT '',
  full_name TEXT DEFAULT '',
  email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  ig_username TEXT DEFAULT '',
  profile_pic TEXT DEFAULT '',
  gender TEXT DEFAULT '',
  locale TEXT DEFAULT '',
  subscribed_at TIMESTAMPTZ,
  last_interaction TIMESTAMPTZ,
  last_seen TIMESTAMPTZ,
  ig_last_interaction TIMESTAMPTZ,
  opted_in_ig BOOLEAN DEFAULT false,
  opted_in_email BOOLEAN DEFAULT false,
  tags JSONB DEFAULT '[]',
  custom_fields JSONB DEFAULT '{}',
  trigger_count INT DEFAULT 0,
  conversation_count INT DEFAULT 0,
  interest_level TEXT DEFAULT 'new',
  heat_score INT DEFAULT 0,
  funnel_stage TEXT DEFAULT 'subscriber',
  flodesk_synced BOOLEAN DEFAULT false,
  do_not_contact BOOLEAN DEFAULT false,
  synced_at TIMESTAMPTZ DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

### manychat_triggers

```sql
CREATE TABLE IF NOT EXISTS manychat_triggers (
  id BIGSERIAL PRIMARY KEY,
  keyword TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  description TEXT DEFAULT '',
  product_url TEXT DEFAULT '',
  is_active BOOLEAN DEFAULT true,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

### subscriber_triggers (logs every keyword fired)

```sql
CREATE TABLE IF NOT EXISTS subscriber_triggers (
  id BIGSERIAL PRIMARY KEY,
  mc_id TEXT NOT NULL,
  keyword TEXT NOT NULL,
  source TEXT DEFAULT 'instagram',
  fired_at TIMESTAMPTZ DEFAULT now(),
  post_id TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### subscriber_conversations

```sql
CREATE TABLE IF NOT EXISTS subscriber_conversations (
  id BIGSERIAL PRIMARY KEY,
  mc_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  message_preview TEXT DEFAULT '',
  flow_name TEXT DEFAULT '',
  channel TEXT DEFAULT 'instagram',
  sent_at TIMESTAMPTZ DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### webhook_events (health monitoring)

```sql
CREATE TABLE IF NOT EXISTS webhook_events (
  id BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL DEFAULT 'trigger',
  source TEXT DEFAULT 'manychat',
  mc_id TEXT DEFAULT '',
  keyword TEXT DEFAULT '',
  subscriber_name TEXT DEFAULT '',
  status TEXT DEFAULT 'success',
  error_message TEXT DEFAULT '',
  payload_preview TEXT DEFAULT '',
  processing_ms INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

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
