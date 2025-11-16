# AWS FinOps Bot

## Overview

AWS FinOps Bot is an AI-driven assistant built to analyze **AWS billing**, **AWS cost optimization**, and **AWS resource usage** using real data from Cost Explorer and Cloud Control API. It combines:

* **Azure OpenAI** as the LLM engine
* **Chainlit** as the interactive web UI
* **MCP servers** for AWS data retrieval:

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

1. Uncomment `data-migration` in `docker-compose.yml`.
2. Run:

```bash
docker compose up postgres data-migration --build
```

3. Wait for the migration to complete.
4. Stop the containers.
5. Re‑comment the `data-migration` section.

### 4. Start the App

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
* For Localstack, confirm that endpoints are correctly configured.

### Azure OpenAI errors

* Verify API key + deployment name.
* Ensure the model supports functions/tool calling.

---

## License

This project can follow standard open‑source licensing such as **MIT**, **Apache‑2.0**, or as defined by the repository owner.

---

## Credits

* **Azure OpenAI** for LLM capabilities
* **Chainlit** for the UI framework
* **MCP (Model Context Protocol)** for server integration
* **AWS Cost Explorer & Cloud Control API**
* **Localstack**, **PostgreSQL**, **Redis**, Docker ecosystem
