# AwsFinOpsBot - Extended Documentation

This document contains detailed information regarding the configuration, architecture, and troubleshooting of the AwsFinOpsBot.

For a high-level overview and basic setup instructions, please see the [main README](../README.md).

## Table of Contents

- [Guardrails](#guardrails)
- [Environment Variables](#environment-variables)
- [Application Architecture](#application-architecture)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)

---

## Guardrails

The bot enforces configurable guardrails to keep every session within the strictly allowed AWS FinOps scope. Key capabilities:

- **Account/Service Allowlists:** Restricts requests to specific AWS accounts (`ALLOWED_AWS_ACCOUNTS`) or services (`ALLOWED_AWS_SERVICES`).
- **Time-Window Limits:** Blocks queries that exceed maximum lookback (`MAX_LOOKBACK_DAYS`) or forecast (`MAX_FORECAST_DAYS`) windows.
- **Budget Enforcements:** Rejects queries if the inferred session cost violates the `BUDGET_POLICY_JSON` configuration.
- **Tool Rate Limiting:** Enforces maximum calls per seconds limits (`TOOL_RATE_LIMITS_JSON`) to prevent excessive iterative downstream usage.
- **Content Scanning:** Employs lightweight detection logic preventing sensitive terminology or injection-related requests (e.g. `password`, `secret access key`, `drop table`) on user inputs, tool outputs, and LLM responses.
- **Auditing:** Emits formal, structured JSON lines tracking all guardrail invocations to the designated `GUARDRAIL_AUDIT_LOG` path.

Set `TOOL_RATE_LIMIT_MODE` inside `guardrails.env` to control enforcement: `enforce` (strictly block requests), `warn` (log warning but continue), or `off` (completely disable rate limiting). The sample `guardrails.env` defaults to `warn` so that local development and testing sessions are not actively interrupted even when iterative loop tools fire repeatedly.

---

## Environment Variables

The application uses several environment variables for configuration. These are split across multiple `.env` files in the `docker-compose.yml` setup. You can find all the example env vars files in [secrets](../secrets) directory. Please update as necessary in the respective `.env` files.

| Variable | File | Default | Description |
| :--- | :--- | :--- | :--- |
| **AI Orchestration** | | | |
| `AI_PROVIDER` | `llm.env` | `AZURE_OPEN_AI` | Controls the active model backend. Valid values: `AZURE_OPEN_AI` or `OLLAMA`. |
| **Azure OpenAI Configuration** | | | |
| `OPENAI_API_VERSION` | `llm.env` | `2025-01-01-preview` | API version for Azure OpenAI |
| `AZURE_OPENAI_MODEL` | `llm.env` | `gpt-5` | Model deployment name |
| `AZURE_OPENAI_ENDPOINT` | `llm.env` | - | Azure OpenAI Endpoint URL |
| `AZURE_OPENAI_API_KEY` | `llm.env` | - | **Secret**: API Key for Azure OpenAI |
| `AZURE_OPENAI_API_KEY2` | `llm.env` | - | **Secret**: Secondary API Key (Optional) |
| **Local Ollama Support** | | | |
| `OLLAMA_BASE_URL` | `llm.env` | `http://host.docker.internal:11434` | Base URL for Ollama |
| `OLLAMA_MODEL` | `llm.env` | `qwen3.5:4b` | Ollama model name |
| `OLLAMA_TEMPERATURE` | `llm.env` | `1.0` | Temperature |
| `OLLAMA_TOP_P` | `llm.env` | `0.95` | Top P parameter |
| `OLLAMA_TOP_K` | `llm.env` | `20` | Top K parameter |
| `OLLAMA_PRESENCE_PENALTY` | `llm.env` | `1.5` | Presence Penalty |
| **AWS Credentials** | | | |
| `AWS_ACCESS_KEY_ID` | `aws.env` | - | **Secret**: AWS Access Key ID |
| `AWS_SECRET_ACCESS_KEY` | `aws.env` | - | **Secret**: AWS Secret Access Key |
| `AWS_DEFAULT_REGION` | `aws.env` | `us-east-1` | Default AWS Region selection |
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
| `BUDGET_POLICY_JSON` | `guardrails.env` | `{}` | JSON configuration for budget enforcement via `monthly_limit_usd` |
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
   - `aws-billing-cost-management-mcp-server` → Queries Billing data & Cost Explorer
   - `aws-api-mcp-server` → Queries general AWS APIs (EC2, CloudWatch, etc.)
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

![Architecture](./architecture.png)

1. **User Interface (Chainlit)**: The entry point for FinOps analysts and developers.
2. **Persistence & Auth**: PostgreSQL stores conversation history (managed by Chainlit Datalayer), while Redis handles fast user-authentication tracking.
3. **Orchestration Layer (LangGraph)**: Manages state and coordinates tool-calling loops reliably. It ensures LLM context size and loops do not exceed limits.
4. **LLM Engine (Azure OpenAI)**: Evaluates user queries and orchestrates the needed tool calls based on context, injecting interactive suggestions (buttons) on response completion.
5. **Data Retrieval (MCP Servers)**: The system utilizes 6 specialized MCP servers to securely bridge the LLM with AWS:
   - **[AWS API MCP Server](https://awslabs.github.io/mcp/servers/aws-api-mcp-server)**: General-purpose direct interaction with any AWS service API. Highly capable of reading resource configurations, executing operational commands, and querying domain-specific services like EC2, S3, or CloudWatch endpoints.
   - **[AWS Documentation MCP Server](https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server)**: Retrieves the most up-to-date AWS service documentation, API limits, and architecture best practices.
   - **[AWS Pricing MCP Server](https://awslabs.github.io/mcp/servers/aws-pricing-mcp-server)**: Accesses the AWS Price List API for retrieving exact service pricing information and expected cost comparisons.
   - **[AWS Billing & Cost Management MCP Server](https://awslabs.github.io/mcp/servers/billing-cost-management-mcp-server)**: Tailored access to billing data, native Cost Explorer insights, historical invoices, budget management, and savings plans optimizations.
   - **[AWS CloudTrail MCP Server](https://awslabs.github.io/mcp/servers/cloudtrail-mcp-server)**: Access to CloudTrail logging events for auditing provisioning patterns, user activity, and holistic security analysis.
   - **[AWS IaC MCP Server](https://awslabs.github.io/mcp/servers/aws-iac-mcp-server)**: Provides detailed Infrastructure as Code insights covering Terraform, CloudFormation, and other deployment modules.

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
- **Action**: It calls the `aws-billing-cost-management-mcp-server` to fetch the 6-month trend. It then calls the `aws-api-mcp-server` to check for low CPU utilization across instances, and `aws-pricing-mcp-server` to determine expected savings.
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

### Azure OpenAI errors

- Verify API key + deployment name.
- Ensure the model supports functions/tool calling.

### MCP Server Connection Issues

- If the AI struggles to retrieve AWS data, confirm that your authentication works:
  - **Using IAM User**: Ensure you have configured the `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION` correctly in `aws.env`.
  - **Using IAM Role**: If relying on an Instance Profile container role, ensure the IAM role is properly attached to the compute host and contains the required policies. Verify `AWS_DEFAULT_REGION` is still populated.
- Review Docker service logs for the `mcp-servers` container to identify transport or timeout errors.

---

## Credits

- **Azure OpenAI** for LLM capabilities
- **LangGraph** for agent orchestration
- **Chainlit** for the UI framework
- **AWS MCP Servers** for AWS API, Billing, Pricing, and CloudTrail
- **Localstack**, **PostgreSQL**, **Redis**, Docker ecosystem
