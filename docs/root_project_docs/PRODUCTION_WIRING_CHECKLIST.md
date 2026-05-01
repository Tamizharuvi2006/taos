# TAOS Production Wiring Checklist (Next.js + Railway + Firebase + Cloudflare)

## 1) Backend Auth Middleware (Railway)
- Install `firebase-admin`.
- Set env vars:
  - `FIREBASE_PROJECT_ID`
  - `FIREBASE_PRIVATE_KEY`
  - `FIREBASE_CLIENT_EMAIL`
- Ensure protected routes require `Authorization: Bearer <ID_TOKEN>`.
- Status codes:
  - `401` missing/invalid token
  - `403` valid token but forbidden scope

## 2) SSE Streaming Contract
- Endpoint: `POST /execute/stream`
- Content type: `text/event-stream`
- Event names:
  - `START`, `STEP_EXECUTED`, `AGENT_SELECTED`, `DEBATE_STARTED`, `DEBATE_RESULT`, `FINAL`, `ERROR`, `PING`
- Heartbeat:
  - `PING` every `SSE_HEARTBEAT_SECONDS` (default 12s)
- Global budget:
  - `MAX_REQUEST_TIME_SECONDS` (default 25s)

## 3) Next.js Frontend Integration (separate repo)
- Login with Firebase client SDK.
- Fetch ID token and attach to all protected backend calls:
  - `Authorization: Bearer <ID_TOKEN>`
- Streaming:
  - Subscribe to SSE events and update UI progressively.
- Retry policy:
  - Retry one time only for network/5xx.
  - No retry for `401/403/429`.

## 4) Deployment and Security
- Railway:
  - `OPENROUTER_API_KEY`
  - Firebase admin env vars
  - `STORAGE_BACKEND=firebase`
- Vercel:
  - backend URL
  - Firebase public client config
- Cloudflare:
  - WAF on
  - Rate limits on `/execute*`, `/tasks*`, `/feedback`, `/progress*`
  - HTTPS enforced

## 5) Ops
- Warmup endpoint: `GET /warmup`
- Schedule warmup ping every 5 minutes.
- Track `request_id` through frontend -> backend -> logs -> storage.

---

## 6) Copy-Paste Deployment Quickstart

### Railway backend env (required)
```env
OPENROUTER_API_KEY=replace_me
SERPER_API_KEY=replace_me

FIREBASE_PROJECT_ID=replace_me
FIREBASE_CLIENT_EMAIL=replace_me
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"

STORAGE_BACKEND=firebase

MAX_REQUEST_TIME_SECONDS=25
SSE_HEARTBEAT_SECONDS=12

ENABLE_PLANNER_SERVICE=true
ENABLE_RESEARCH_SERVICE=true
ENABLE_EXECUTION_SERVICE=true
ENABLE_DEBATE_SERVICE=false

SERVICE_CIRCUIT_FAILURES=5
SERVICE_CIRCUIT_COOLDOWN_SECONDS=60

EXECUTION_SAMPLING_RATE=0.2
EXECUTION_STORE_LATENCY_THRESHOLD_MS=3000
MAX_EXECUTION_HISTORY_RECORDS=300
MAX_PLAN_HISTORY_RECORDS=300
MAX_STORED_FIELD_BYTES=8192
```

### Cloudflare API protection (recommended)
```txt
WAF: Enabled (managed rules on)

Rate limits:
- /execute*   -> per IP and/or user token limits
- /tasks*     -> per IP and/or user token limits
- /feedback   -> per IP and/or user token limits
- /progress*  -> per IP and/or user token limits

SSE note:
- Keep idle timeout compatible with server heartbeat PING every 12s
```

### Next.js client contract (separate repo)
```ts
// Retry once for network/5xx only; never retry 401/403/429.
const NON_RETRY = new Set([401, 403, 429]);

export async function executeWithOneRetry(
  body: unknown,
  token: string,
  requestId: string
) {
  const call = () =>
    fetch(`${process.env.NEXT_PUBLIC_API_URL}/execute`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Request-ID": requestId,
      },
      body: JSON.stringify(body),
    });

  let res: Response;
  try {
    res = await call();
  } catch {
    res = await call();
    return res;
  }

  if (res.status >= 500) return call();
  if (NON_RETRY.has(res.status)) return res;
  return res;
}
```

```txt
SSE expected event types:
START, AGENT_SELECTED, STEP_EXECUTED, DEBATE_STARTED, DEBATE_RESULT, FINAL, ERROR, PING
```
