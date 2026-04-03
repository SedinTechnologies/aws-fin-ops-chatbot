<div align="center">
  <h1>AWS FinOps Bot</h1>
  <p><strong>An AI-driven assistant built to analyze AWS billing, optimize costs, and track resource usage.</strong></p>
</div>

---

## 📖 Overview

**AWS FinOps Bot** combines the power of **Azure OpenAI** with real AWS data from **Cost Explorer** and the **Cloud Control API**. Built on top of a robust **LangGraph** orchestration framework and using **MCP (Model Context Protocol) servers**, the bot provides interactive, strictly domain-bound insights into your cloud infrastructure via a sleek **Chainlit** web UI.

Whether you want to analyze spending trends, find unutilized resources, or set up customizable cost guardrails, the FinOps bot handles it natively and securely.

![AWS FinOps Bot Architecture](docs/aws_finops_architecture.png)

### 🌟 Key Features

* **AWS Billing & Cost Analytics**: Break down costs by service, region, tags, or usage type. Instantly detect monthly spend trends and anomalies.
* **AWS Resource Usage Insights**: Fetch resource inventory summaries and identify underutilized or abandoned provisions.
* **Strict Domain-Bound Guardrails**: The bot enforces strict policies to politely reject non-AWS domain queries. Easily restrict access via Account/Service allowlists and enforce rate limits.
* **Interactive Chat UI**: Smart follow-up suggestions, rich Markdown formatting, and easy-to-use action buttons, perfectly persisted via PostgreSQL & Redis.
* **Dockerized for Quick Setup**: With **Localstack** emulating S3 storage exclusively for Chainlit persistence (while all MCP servers query your real AWS account) and a simple **docker-compose** setup, a local application can be spun up in minutes.

---

## 🚀 Quick Setup Instructions

Follow these steps to get a local development environment running quickly so you can start chatting with your AWS data!

### Prerequisites

* [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed.
* Valid API Keys (Azure OpenAI) and appropriate AWS authentication credentials.

### 1. Clone the Repository

Clone the project to your local machine and navigate into the directory:

```bash
git clone https://github.com/SedinTechnologies/aws-fin-ops-chatbot.git
cd aws-fin-ops-chatbot
```

### 2. Configure AWS IAM Role & Credentials

The bot requires an AWS IAM Role (or User) with specific permissions to query your AWS environment.

1. **Create an IAM User (or Role)** in your AWS console (e.g., `aws-finops-bot-user`).
2. **Attach Policies** to the user/role to grant appropriate access:
   * Each MCP server requires distinct AWS IAM permissions. Please see the official documentation for the minimum required policies or attach appropriate managed policies (e.g., `ReadOnlyAccess`, `AWSBillingReadOnlyAccess`):
     * [AWS API MCP Server Permissions](https://awslabs.github.io/mcp/servers/aws-api-mcp-server#-credential-management-and-access-control)
     * [AWS Pricing MCP Server Permissions](https://awslabs.github.io/mcp/servers/aws-pricing-mcp-server#prerequisites)
     * [AWS Billing & Cost Management MCP Server Permissions](https://awslabs.github.io/mcp/servers/billing-cost-management-mcp-server#aws-authentication)
     * [AWS CloudTrail MCP Server Permissions](https://awslabs.github.io/mcp/servers/cloudtrail-mcp-server#required-iam-permissions)
     * [AWS IaC MCP Server Permissions](https://awslabs.github.io/mcp/servers/aws-iac-mcp-server#iam-permissions)

3. **Generate an Access Key** for this IAM User (or obtain credentials for the Role).
4. **Update Configuration**: Add the generated Access Key ID and Secret Access Key to the `aws.env` file in the [secrets](secrets/) directory of the repository:

   ```env
   AWS_ACCESS_KEY_ID=your_access_key_here
   AWS_SECRET_ACCESS_KEY=your_secret_key_here
   ```

### 3. Configure Other Environment Variables

The application relies on several other environment files (`azure-openai.env`, `chainlit.env`, etc.). You must provide the correct keys/values before proceeding.

* 👉 **[See the Complete Detailed list of Environment Variables inside docs/EXTENDED_README.md](docs/EXTENDED_README.md#environment-variables)**

### 4. Prepare the Database Migrations

Set up your PostgreSQL database using the built-in Chainlit datalayer migrations:

1. Start the `data-migration` service which will run the migrations required for the Chainlit datalayer:

   ```bash
   docker compose up data-migration
   ```

2. After the data migration container exists, please check for the success message in the terminal logs to confirm that the migrations have completed successfully.

### 5. Start the Application

Start the full stack (Chainlit App, PostgreSQL, Redis, and Localstack) in the background:

  ```bash
  docker compose up --build -d
  ```

Ensure all the services are running. You can check the status of the services by running the following command:

```bash
docker compose ps
```

You should see `chainlit-ui`, `redis`, `mcp-servers`, `postgres` and `localstack` services running. If not, please troubleshoot the issue by checking the logs of the respective services.

### 6. Create a Chainlit Login User (Redis Authentication)

By default, the application enforces login through Chainlit, authenticating against a Redis backend. We provide a `scripts/signup.py` script to generate a user with an associated AWS Role ARN. Please replace the values of `USER_ID`, `DISPLAY_NAME`, and `PASSWORD` accordingly and then run the following command:

   ```bash
   docker compose exec -it chainlit-ui bash -c "USER_ID='[USER_ID]' \
   DISPLAY_NAME='[DISPLAY_NAME]' \
   PASSWORD='[PASSWORD]' \
   python scripts/signup.py"
   ```

   Upon successful execution, you should see the following output:

   ```text
    Stored user user:[USER_ID] in Redis.
   ```

* You can now login into the application at: **🔗 [http://localhost:8000](http://localhost:8000)** and start chatting with the bot.

---

## 📚 Advanced Documentation

For any low-level details, we've organized everything in the `docs` folder. New developers are recommended to look through these resources once they have their local environment up and running.

* **[Architecture, Environment Config & Troubleshooting](docs/EXTENDED_README.md)**: Deep dive into the flow, the exhagustive env var list, and common bug troubleshooting.
* **[LangGraph Implementation](docs/langgraph_implementation_and_workflow.md)**: Understand the LangGraph workflow layout.
* **[Available MCP Servers & Tooling](docs/available_mcp_tools.md)**: Discover all integrated tool definitions.
* **[LangGraph Migration & Prototype](docs/langgraph_migration.md)**: Read the backstory and transition details for the underlying orchestration layer.
