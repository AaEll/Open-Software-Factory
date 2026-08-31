# PromptPay integration

OSF generates site files in an isolated worker workspace via **Fireworks**; [PromptPay](https://github.com/AaEll/PromptPay)
publishes previews, checks domains, and handles payments. The agent never sees raw credentials.

**Local repos (side by side):**

| Repo | Path |
|---|---|
| PromptPay | `/Users/claudio/github/PromptPay` |
| Open Software Factory | `/Users/claudio/github/Open-Software-Factory` |

---

## Setup

### 1. PromptPay broker

```bash
cd /Users/claudio/github/PromptPay
pip install -e ".[dev,stripe]"

export PROMPTPAY_ENC_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export PROMPTPAY_ALLOWLIST=cloudflare
export PROMPTPAY_BUDGETS=mvp-demo:5000

uvicorn promptpay.app:default_app --factory --host 127.0.0.1 --port 8090
```

Verify: `curl http://127.0.0.1:8090/healthz` → `{"healthy":true}`

### 2. OSF + Fireworks (real model)

```bash
cd /Users/claudio/github/Open-Software-Factory
pip install -e ".[agent]"
cp .env.example .env
```

Edit `.env` (gitignored — **never commit real keys**):

```bash
FIREWORKS_API_KEY=fw_...          # from https://fireworks.ai — required for live LLM
PROMPTPAY_URL=http://127.0.0.1:8090

# Optional MVP overrides
OSF_PROMPT=A landing page for a specialty coffee shop in Denver called Mountain Brew
OSF_DOMAIN=42protein.com
OSF_OBJECTIVE_ID=mvp-demo          # must match PROMPTPAY_BUDGETS key

# Optional model override (default: kimi-k2p7-code)
# OSF_MODEL=fireworks/accounts/fireworks/models/kimi-k2p7-code
```

Without `FIREWORKS_API_KEY`, `evals.mvp_promptpay` uses a scripted offline worker (no LLM).

---

## Run the MVP pipeline (real model)

```bash
python -m evals.mvp_promptpay
```

Flow:

1. **Fireworks worker** builds `index.html` (+ assets) from `OSF_PROMPT`
2. `POST /domains/preview` on PromptPay (when `OSF_DOMAIN` is set)
3. `POST /previews` with workspace files → Cloudflare temporary URL

Example output:

```
runtime: fireworks/accounts/fireworks/models/kimi-k2p7-code
worker outcome: completed
preview_url: https://....workers.dev
```

---

## Fireworks engine

| Item | Value |
|---|---|
| Env var | `FIREWORKS_API_KEY` (or alias `FIREWORKS`) |
| Key format | `fw_…` |
| API | `https://api.fireworks.ai/inference/v1` (OpenAI-compatible) |
| Default model | `accounts/fireworks/models/kimi-k2p7-code` |
| Code | `osf/engines/fireworks.py` |

---

## Programmatic use

```python
from osf.promptpay import PromptPayClient, publish_objective_site
from osf.types import Workspace

client = PromptPayClient.from_env()  # reads PROMPTPAY_URL
result = publish_objective_site(
    Workspace(path="/path/to/workspace", handle="ws"),
    objective_id="mvp-demo",
    domain="example.com",
    client=client,
)
print(result.preview["preview_url"])
```

Policy gate before vendor spend:

```python
client.authorize(
    objective_id="mvp-demo",
    vendor="cloudflare",
    amount_cents=1200,
    purpose="domain registration",
)
```

---

## PromptPay browser demo

PromptPay also ships an interactive wireframe at `/demo/mvp.html` (phone sign-in → prompt → domain →
preview → payment). That UI still **stubs** site generation in the browser. Use `evals.mvp_promptpay`
here for a real Fireworks-generated site.

See PromptPay [`docs/osf-integration.md`](https://github.com/AaEll/PromptPay/blob/main/docs/osf-integration.md)
and [`docs/mvp.md`](https://github.com/AaEll/PromptPay/blob/main/docs/mvp.md).
