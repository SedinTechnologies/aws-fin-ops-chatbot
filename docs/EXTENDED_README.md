# AWS FinOps Bot - Detailed Documentation

This document contains detailed information regarding the configuration, architecture, and troubleshooting of the AWS FinOps Bot.

For a high-level overview and basic setup instructions, please see the [main README](../README.md).

## Table of Contents

- [Guardrails](#guardrails)
- [Environment Variables](#environment-variables)
- [Application Architecture](#application-architecture)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)

---

## Guardrails

The bot enforces configurable guardrails to keep every session within allowed AWS-finops scope. Key capabilities:

- **Account/Service allowlists:** restrict requests to specific AWS accounts or services.
- **Time-window limits:** block queries that exceed maximum lookback or forecast windows.
- **Tool rate limiting:** per-tool call limits to prevent excessive downstream usage.
- **Content scanning:** lightweight keyword detection on user input, tool output, and model responses.
- **Auditing:** structured JSON lines written to the path in `GUARDRAIL_AUDIT_LOG`.

Set `TOOL_RATE_LIMIT_MODE` to control enforcement: `enforce` (block requests), `warn` (log but continue), or `off` (disable rate limiting). The sample `chainlit.env` defaults to `warn` so development sessions are not interrupted even when a tool is called repeatedly.

---

## Environment Variables

The application uses several environment variables for configuration. These are split across multiple `.env` files in the `docker-compose.yml` setup. You can find all the example env vars files in [secrets](../secrets) directory. Please update as necessary in the respective `.env` files.

| Variable | File | Default | Description |
| :--- | :--- | :--- | :--- |
| **Azure OpenAI** | | | |
| `OPENAI_API_VERSION` | `azure-openai.env` | `2025-01-01-preview` | API version for Azure OpenAI |
| `AZURE_OPENAI_MODEL` | `azure-openai.env` | `gpt-5` | Model deployment name |
| `AZURE_OPENAI_ENDPOINT` | `azure-openai.env` | - | Azure OpenAI Endpoint URL |
| `AZURE_OPENAI_API_KEY` | `azure-openai.env` | - | **Secret**: API Key for Azure OpenAI |
| `AZURE_OPENAI_API_KEY2` | `azure-openai.env` | - | **Secret**: Secondary API Key (Optional) |
| **AWS Credentials** | | | |
| `AWS_ACCESS_KEY_ID` | `aws.env` | - | **Secret**: AWS Access Key ID |
| `AWS_SECRET_ACCESS_KEY` | `aws.env` | - | **Secret**: AWS Secret Access Key |
| `AWS_REGION` | `aws.env` | `us-east-1` | AWS Region for App |
| **Chainlit Config** | | | |
| `CHAINLIT_HOST` | `chainlit.env` | `0.0.0.0` | Host for Chainlit server |
| `CHAINLIT_PORT` | `chainlit.env` | `8000` | Port for Chainlit server |
| `CHAINLIT_LANGUAGE` | `chainlit.env` | `en-US` | UI Language |
| `CHAINLIT_REQUIRE_LOGIN` | `chainlit.env` | `true` | Enforce login |
| `CHAINLIT_AUTH_SECRET` | `chainlit.env` | - | **Secret**: Secret for session signing |
| **Database & Cache** | | | |
| `REDIS_HOST` | `chainlit.env` | `redis` | Redis hostname |
| `REDIS_PORT` | `chainlit.env` | `6379` | Redis port |
| `DATABASE_URL` | `chainlit.env` | `postgresql://root:root@postgres:5432/postgres` | **Secret**: PostgreSQL connection string |
| **App Specific AWS** | | | |
| `BUCKET_NAME` | `chainlit.env` | `aws-fin-ops-bot-data` | S3 Bucket name |
| `APP_AWS_ACCESS_KEY` | `chainlit.env` | `dummy-key` | AWS Access Key for App (Localstack) |
| `APP_AWS_SECRET_KEY` | `chainlit.env` | `dummy-key` | AWS Secret Key for App (Localstack) |
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
| **LangGraph Config** | | | |
| `LANGGRAPH_MAX_TOOL_LOOPS` | `langgraph.env` | `60` | Max tool loops |
| `LANGGRAPH_RECURSION_LIMIT` | `langgraph.env` | `40` | Recursion limit |
| `STREAMABLE_HTTP_READY_TIMEOUT` | `langgraph.env` | `25` | Streamable HTTP ready timeout (seconds) |
| `STREAMABLE_HTTP_READY_INITIAL_DELAY` | `langgraph.env` | `2` | Streamable HTTP ready initial delay (seconds) |

---

## Application Architecture

### High-Level Flow

1. **User logs into the Chainlit UI** using the configured authentication provider (Redis for local development or an external identity provider in production).
2. **User initiates a chat session** and submits a cost‑related or usage‑related query.
3. **System Prompt applies strict domain rules**, ensuring only AWS billing and AWS resource‑usage queries are processed.
4. **LangGraph Orchestration**:
   - The request is processed by a `StateGraph` workflow.
   - The LLM decides whether to call tools or generate a response.
5. If tools are needed, the LLM invokes the required **MCP servers**. Examples include:
   - `aws-cost-explorer-mcp-server` → Queries Cost Explorer
   - `aws-ccapi-mcp-server` → Queries Cloud Control API
6. MCP servers execute the underlying AWS requests and return structured results.
7. The processed response is sent back to Chainlit, which **renders it in the UI**.
8. **Local Development**:
   - **Localstack** emulates AWS S3 exclusively for Chainlit persistence operations. **All MCP servers fetch data securely from actual AWS Cloud Services** via your real AWS account credentials.
   - Redis functions as the authentication provider.

### Production Environment vs Local Development

**Production Environment Recommendations**:

- A real S3 bucket must be used instead of Localstack.
- Real user authentication (SSO or your provider of choice) should be configured.
- Redis can still be used as an auth store if preferred.

---

## Detailed Architecture & Data Flow

![AWS FinOps Bot Architecture](./aws_finops_architecture.png)

1. **User Interface (Chainlit)**: The entry point for FinOps analysts and developers.
2. **Persistence & Auth**: PostgreSQL stores conversation history (managed by Chainlit Datalayer), while Redis handles fast user-authentication tracking.
3. **Orchestration Layer (LangGraph)**: Manages state and coordinates tool-calling loops reliably. It ensures LLM context size and loops do not exceed limits.
4. **LLM Engine (Azure OpenAI)**: Evaluates user queries and orchestrates the needed tool calls based on context, injecting interactive suggestions (buttons) on response completion.
5. **Data Retrieval (MCP Servers)**: The system utilizes 6 specialized MCP servers to securely bridge the LLM with AWS:
   - **Cost Explorer MCP**: Analyzes costs by dimension, tags, and forecasts.
   - **Cloud Control API (CCAPI) MCP**: Enumerates running resource states.
   - **CloudWatch MCP**: Retrieves operational metrics (e.g. CPU/Memory) to identify underutilization.
   - **Billing MCP**: Fetches billing profiles and invoices.
   - **CloudTrail MCP**: Audits historical events and resource modifications.
   - **Pricing MCP**: Queries the AWS Price List API for expected resource costs.

---

## Detailed Walkthrough

Below is a detailed walkthrough of how a typical user interaction unfolds within the FinOps Bot.

### 1. Authentication

- **Step**: The user accesses the Chainlit UI on `http://localhost:8000`.
- **Action**: They are presented with a login screen. They must log in using the credentials configured via `scripts/signup.py`.
- **Result**: Upon successful login, the user's specific IAM Role ARN and associated MCP connections are dynamically registered into their active session.

### 2. Issuing a Query

- **Step**: The user types a query like, *"Show my monthly AWS spend trend for the last 6 months and suggest rightsizing opportunities."*
- **Action**: The LangGraph engine routes the query to Azure OpenAI.
- **Guardrails Check**: The input is scanned. If it asks about a forbidden topic (e.g., "Write me a python script to hack a DB"), the GuardrailEngine intercepts and replies politely with a domain violation message.

### 3. Tool Execution & Data Stitching

- **Step**: The LLM determines it needs data.
- **Action**: It calls the `aws-cost-explorer-mcp-server` to fetch the 6-month trend. It then calls the `aws-cloudwatch-mcp-server` to check for low CPU utilization across instances, and `aws-pricing-mcp-server` to determine expected savings.
- **Result**: Data is returned asynchronously back to the LangGraph node and interpreted by the LLM.

### 4. Interactive Response

- **Step**: The LLM compiles the final Markdown-formatted response.
- **Action**: The bot streams the response into the UI. Once finished, it appends **"Action Buttons"** (e.g., 👉 *Compare forecast vs actual*, 👉 *Show EC2 breakdown by tag*).
- **Result**: The user can click these buttons to instantly trigger the next phase of their investigation without re-typing context.

---

## Troubleshooting

### Migration container keeps running

- Ensure that the Chainlit version matches the datalayer migrations.
- Inspect logs:

```bash
docker logs data-migration
```

### Chainlit UI not loading

- Confirm containers are healthy:

```bash
docker compose ps
```

- Ensure port `8000` is not in use.

### MCP servers failing

- Check your AWS credentials.
- Ensure the MCP env vars still point to `127.0.0.1` (servers run inside the Chainlit container). If you override them, the hostname must exist on the Docker network or you must set `ENFORCE_LOCAL_MCP=false` and supply matching DNS.
- For Localstack, confirm that endpoints are correctly configured.
- If a streamable MCP takes a while to boot (e.g., first launch after pulling images), bump `STREAMABLE_HTTP_READY_TIMEOUT` (default `30s`) and optionally `STREAMABLE_HTTP_READY_INITIAL_DELAY` (default `1s`) so the readiness probe waits long enough before falling back to stdio.
- Local development without the HTTP transport? Set `AWS_COST_EXPLORER_MCP_TRANSPORT=AWS_CCAPI_MCP_TRANSPORT=stdio` in `chainlit.env` to skip streamable startup entirely and avoid long login delays.

### Azure OpenAI errors

- Verify API key + deployment name.
- Ensure the model supports functions/tool calling.

---

## Credits

- **Azure OpenAI** for LLM capabilities
- **LangGraph** for agent orchestration
- **Chainlit** for the UI framework
- **MCP (Model Context Protocol)** for server integration
- **AWS Cost Explorer & Cloud Control API**
- **Localstack**, **PostgreSQL**, **Redis**, Docker ecosystem
