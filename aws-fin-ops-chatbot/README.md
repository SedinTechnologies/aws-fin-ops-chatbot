# AWS FinOps Bot

## Overview

AWS FinOps Bot is an AI-driven assistant built to analyze **AWS billing**, **AWS cost optimization**, and **AWS resource usage** using real data from Cost Explorer and Cloud Control API. It combines:

* **Azure OpenAI** as the LLM engine
* **Chainlit** as the interactive web UI
* **MCP servers** for AWS data retrieval (launched inside the Chainlit container via streamable HTTP; all endpoints stay on `127.0.0.1` unless you set `ENFORCE_LOCAL_MCP=false`):

  * `aws-cost-explorer-mcp-server`
  * `aws-ccapi-mcp-server`
* **PostgreSQL** for persistent storage
* **Redis** for authentication - better to replace for production as mentioned below
* **Localstack** to emulate AWS S3 service during development

Users authenticate into the Chainlit interface, submit AWS cost-related queries, and the bot retrieves real AWS data through MCP servers before returning structured insights.

In production, a **real S3 bucket** and **real identity provider (SSO or similar)** should be used, while Redis remains optional for auth.

The bot strictly follows a system policy ensuring that all responses stay exclusively within AWS billing and resource-usage topics.

---

## Features

* **AWS Billing & Cost Analytics**

  * Retrieve Cost Explorer metrics
  * Break down costs by service, region, usage type, tags, accounts
  * Analyze monthly spend trends
  * Detect anomalies

* **AWS Resource Usage Insights**

  * Fetch AWS resource inventory through Cloud Control API
  * Summaries of provisioned resources
  * Identify unused or underutilized resources

* **Strict Domain‑Bound Responses**

  * Responses are limited to AWS billing and resource‑usage analysis
  * Queries outside the domain are rejected politely

* **Chainlit UI with Persistence**

  * PostgreSQL backend via `chainlit-datalayer`
  * Redis for user authentication during development and optionally in production

* **Development Support Tools**

  * Localstack for offline AWS API simulation
  * Dockerized architecture for easy setup

---

## Guardrails

The bot enforces configurable guardrails to keep every session within allowed AWS-finops scope. Key capabilities:

- **Account/Service allowlists:** restrict requests to specific AWS accounts or services.
- **Time-window limits:** block queries that exceed maximum lookback or forecast windows.
- **Tool rate limiting:** per-tool call limits to prevent excessive downstream usage.
- **Content scanning:** lightweight keyword detection on user input, tool output, and model responses.
- **Auditing:** structured JSON lines written to the path in `GUARDRAIL_AUDIT_LOG`.

Set `TOOL_RATE_LIMIT_MODE` to control enforcement: `enforce` (block requests), `warn` (log but continue), or `off` (disable rate limiting). The sample `chainlit.env` defaults to `warn` so development sessions are not interrupted even when a tool is called repeatedly.

## Environment Variables

The application uses several environment variables for configuration. These are split across multiple `.env` files in the `docker-compose.yml` setup. Please update as necessary in the respective `.env` files.

| Variable | File | Default | Description |
| :--- | :--- | :--- | :--- |
| **Azure OpenAI** | | | |
| `OPENAI_API_VERSION` | `azure-openai.env` | `2025-01-01-preview` | API version for Azure OpenAI |
| `AZURE_OPENAI_MODEL` | `azure-openai.env` | `gpt-5` | Model deployment name |
| `AZURE_OPENAI_ENDPOINT` | `azure-openai.env` | `https://opendevopsai.openai.azure.com` | Endpoint URL |
| `AZURE_OPENAI_API_KEY` | `azure-openai.env` | - | **Secret**: API Key for Azure OpenAI |
| `AZURE_OPENAI_API_KEY2` | `azure-openai.env` | - | **Secret**: Secondary API Key |
| **AWS Credentials** | | | |
| `AWS_ACCESS_KEY_ID` | `aws-rf-billingpoc-user.env` | - | **Secret**: AWS Access Key ID |
| `AWS_SECRET_ACCESS_KEY` | `aws-rf-billingpoc-user.env` | - | **Secret**: AWS Secret Access Key |
| `AWS_DEFAULT_REGION` | `aws-rf-billingpoc-user.env` | `us-east-1` | Default AWS Region |
| **Chainlit Config** | | | |
| `CHAINLIT_HOST` | `chainlit.env` | `0.0.0.0` | Host for Chainlit server |
| `CHAINLIT_PORT` | `chainlit.env` | `8000` | Port for Chainlit server |
| `CHAINLIT_LANGUAGE` | `chainlit.env` | `en-US` | UI Language |
| `CHAINLIT_REQUIRE_LOGIN` | `chainlit.env` | `true` | Enforce login |
| `CHAINLIT_AUTH_SECRET` | `chainlit.env` | - | **Secret**: Secret for session signing |
| **Database & Cache** | | | |
| `REDIS_HOST` | `chainlit.env` | `redis` | Redis hostname |
| `REDIS_PORT` | `chainlit.env` | `6379` | Redis port |
| `DATABASE_URL` | `chainlit.env` | - | **Secret**: PostgreSQL connection string |
| **App Specific AWS** | | | |
| `BUCKET_NAME` | `chainlit.env` | `aws-fin-ops-bot-data` | S3 Bucket name |
| `APP_AWS_ACCESS_KEY` | `chainlit.env` | `dummy-key` | AWS Access Key for App (Localstack) |
| `APP_AWS_SECRET_KEY` | `chainlit.env` | `dummy-key` | AWS Secret Key for App (Localstack) |
| `APP_AWS_REGION` | `chainlit.env` | `us-east-1` | AWS Region for App |
| `DEV_AWS_ENDPOINT` | `chainlit.env` | `http://localstack:4566` | Localstack endpoint |
| **Guardrails** | | | |
| `GUARDRAILS_ENABLED` | `guardrails.env` | `true` | Master switch for guardrails |
| `GUARDRAIL_AUDIT_LOG` | `guardrails.env` | `/tmp/guardrail_audit.log` | Path to audit log |
| `ALLOWED_AWS_ACCOUNTS` | `guardrails.env` | - | Comma-separated allowed account IDs |
| `ALLOWED_AWS_SERVICES` | `guardrails.env` | `CostExplorer,EC2,S3` | Comma-separated allowed services |
| `MAX_LOOKBACK_DAYS` | `guardrails.env` | `365` | Max historical days for queries |
| `MAX_FORECAST_DAYS` | `guardrails.env` | `90` | Max forecast days |
| `TOOL_RATE_LIMIT_MODE` | `guardrails.env` | `warn` | Rate limit mode: `enforce`, `warn`, `off` |
| `TOOL_RATE_LIMITS_JSON` | `guardrails.env` | `[]` | JSON for per-tool limits |
| `BUDGET_POLICY_JSON` | `guardrails.env` | `{}` | JSON for budget policy |
| **MCP Servers** | | | |
| `AWS_COST_EXPLORER_MCP_HOST` | `mcp-servers.env` | `127.0.0.1` | Host for Cost Explorer MCP |
| `AWS_COST_EXPLORER_MCP_BIND_HOST` | `mcp-servers.env` | `127.0.0.1` | Bind Host for Cost Explorer MCP |
| `AWS_COST_EXPLORER_MCP_CLIENT_HOST` | `mcp-servers.env` | `127.0.0.1` | Client Host for Cost Explorer MCP |
| `AWS_COST_EXPLORER_MCP_URL` | `mcp-servers.env` | `http://127.0.0.1:8001/mcp` | URL for Cost Explorer MCP |
| `AWS_CCAPI_MCP_HOST` | `mcp-servers.env` | `127.0.0.1` | Host for CCAPI MCP |
| `AWS_CCAPI_MCP_BIND_HOST` | `mcp-servers.env` | `127.0.0.1` | Bind Host for CCAPI MCP |
| `AWS_CCAPI_MCP_CLIENT_HOST` | `mcp-servers.env` | `127.0.0.1` | Client Host for CCAPI MCP |
| `AWS_CCAPI_MCP_URL` | `mcp-servers.env` | `http://127.0.0.1:8002/mcp` | URL for CCAPI MCP |

---

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repo-url>
cd aws-finops-bot
```

### 2. Prepare Database Migrations

If upgrading Chainlit, check for migrations in:
`https://github.com/Chainlit/chainlit-datalayer`

Update the local migrations if necessary.

### 3. Run Migrations

1. Uncomment `data-migration` service code in `docker-compose.yml`.
2. Run:

```bash
docker compose up postgres data-migration --build
```

3. Wait for the migrations to complete.
4. Stop the docker compose command.
5. Re‑comment the `data-migration` service code in `docker-compose.yml`.

### 4. Start the Actual Application & it's dependencies

```bash
docker compose up --build
```

Visit: **[http://localhost:8000](http://localhost:8000)**

---

## Application Architecture

### High-Level Flow

1. **User logs into the Chainlit UI** using the configured authentication provider (Redis for local development or an external identity provider in production).
2. **User initiates a chat session** and submits a cost‑related or usage‑related query.
3. **System Prompt applies strict domain rules**, ensuring only AWS billing and AWS resource‑usage queries are processed.
4. Chainlit forwards the validated query to **Azure OpenAI**.
5. The LLM invokes the required **MCP servers** to fetch real AWS data:

   * `aws-cost-explorer-mcp-server` → Queries Cost Explorer
   * `aws-ccapi-mcp-server` → Queries Cloud Control API
6. MCP servers execute the underlying AWS requests and return structured results.
7. The processed response is sent back to Chainlit, which **renders it in the UI**.
8. **Local Development**:

   * Localstack emulates AWS services, including S3 storage.
   * Redis functions as the authentication provider.

   **Production Environment**:

   * A real S3 bucket must be used instead of Localstack.
   * Real user authentication (SSO or your provider of choice) should be configured.
   * Redis can still be used as an auth store if preferred.

---

## Troubleshooting

### Migration container keeps running

* Ensure that the Chainlit version matches the datalayer migrations.
* Inspect logs:

```bash
docker logs data-migration
```

### Chainlit UI not loading

* Confirm containers are healthy:

```bash
docker compose ps
```

* Ensure port `8000` is not in use.

### MCP servers failing

* Check your AWS credentials.
* Ensure the MCP env vars still point to `127.0.0.1` (servers run inside the Chainlit container). If you override them, the hostname must exist on the Docker network or you must set `ENFORCE_LOCAL_MCP=false` and supply matching DNS.
* For Localstack, confirm that endpoints are correctly configured.
* If a streamable MCP takes a while to boot (e.g., first launch after pulling images), bump `STREAMABLE_HTTP_READY_TIMEOUT` (default `30s`) and optionally `STREAMABLE_HTTP_READY_INITIAL_DELAY` (default `1s`) so the readiness probe waits long enough before falling back to stdio.
* Local development without the HTTP transport? Set `AWS_COST_EXPLORER_MCP_TRANSPORT=AWS_CCAPI_MCP_TRANSPORT=stdio` in `chainlit.env` to skip streamable startup entirely and avoid long login delays.

### Azure OpenAI errors

* Verify API key + deployment name.
* Ensure the model supports functions/tool calling.

---

## Credits

* **Azure OpenAI** for LLM capabilities
* **Chainlit** for the UI framework
* **MCP (Model Context Protocol)** for server integration
* **AWS Cost Explorer & Cloud Control API**
* **Localstack**, **PostgreSQL**, **Redis**, Docker ecosystem
