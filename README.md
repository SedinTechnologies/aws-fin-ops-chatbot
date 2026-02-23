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

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) installed.
- Valid API Keys (Azure OpenAI) and appropriate AWS authentication credentials.

### 1. Clone the Repository

Clone the project to your local machine and navigate into the directory:

```bash
git clone <repo-url>
cd aws-finops-bot
```

### 2. Configure AWS IAM Role & Credentials

The bot requires an AWS IAM Role (or User) with specific permissions to query your AWS environment.

1. **Create an IAM User (or Role)** in your AWS console (e.g., `aws-finops-bot-user`).
2. **Attach Policies** to the user/role to grant appropriate access:
   * **`ReadOnlyAccess`** (AWS Managed Policy): Required for the Cloud Control API, CloudWatch, Billing, CloudTrail, and Pricing MCP servers to read resource configurations and metrics.
   * **Cost Explorer Access**: Required for the Cost Explorer MCP server. You can attach the `AWSBillingReadOnlyAccess` managed policy or create an inline policy with the following permissions:
     ```json
     {
       "Version": "2012-10-17",
       "Statement": [
         {
           "Effect": "Allow",
           "Action": [
             "ce:GetCostAndUsage",
             "ce:GetCostForecast",
             "ce:GetDimensionValues",
             "ce:GetTags"
           ],
           "Resource": "*"
         }
       ]
     }
     ```
3. **Generate an Access Key** for this IAM User (or obtain credentials for the Role).
4. **Update Configuration**: Add the generated Access Key ID and Secret Access Key to the `aws.env` file in the root of the repository:
   ```env
   AWS_ACCESS_KEY_ID=your_access_key_here
   AWS_SECRET_ACCESS_KEY=your_secret_key_here
   ```

### 3. Configure Other Environment Variables

The application relies on several other environment files (`azure-openai.env`, `chainlit.env`, etc.). You must provide the correct keys/values before proceeding.
* 👉 **[See the Complete Detailed list of Environment Variables inside docs/README.md](docs/README.md#environment-variables)**

### 4. Prepare the Database Migrations

Set up your PostgreSQL database using the built-in Chainlit datalayer migrations:

1. Open `docker-compose.yml` and **uncomment** the `data-migration` service code.
2. Build and run the migration containers:
   ```bash
   docker compose up postgres data-migration --build
   ```
3. Wait for the migrations to complete successfully in your terminal logs.
4. Stop the docker compose process (e.g., using `Ctrl+C`).
5. **Re-comment** the `data-migration` service code in `docker-compose.yml`.

### 5. Create a Chainlit Login User (Redis Authentication)

By default, the application enforces login through Chainlit, authenticating against a Redis backend. We provide a `scripts/signup.py` script to generate a user with an associated AWS Role ARN.

1. Ensure the **Redis container** is running, or start it explicitly:
   ```bash
   docker compose up -d redis
   ```
2. Modify the user details at the bottom of `scripts/signup.py` (username, name, password, and the AWS Role ARN you created in Step 2):
   ```python
   # Example in scripts/signup.py
   store_user("your-username", "Your Name", "SecurePassword!", "arn:aws:iam::123456789012:role/aws-finops-bot-user")
   ```
3. Run the script:
   ```bash
   pip install redis bcrypt
   python scripts/signup.py
   ```
   *(The user credentials will be securely hashed and stored in Redis).*

### 6. Start the Application

Start the full stack (Chainlit App, PostgreSQL, Redis, and Localstack) in the background:

```bash
docker compose up --build
```

*(Optional) You can customize the MCP default versions at build time:*
```bash
docker compose build --build-arg AWS_COST_EXPLORER_MCP_SERVER_VERSION=0.2.0
docker compose up
```

Once the containers are successfully running, visit:
**🔗 [http://localhost:8000](http://localhost:8000)**

---

## 📚 Advanced Documentation

For any low-level details, we've organized everything in the `docs` folder. New developers are recommended to look through these resources once they have their local environment up and running.

* **[Architecture, Environment Configs, & Troubleshooting](docs/README.md)**: Deep dive into the flow, the exhaustive env var list, and common bug troubleshooting.
* **[LangGraph Implementation](docs/langgraph_implementation_and_workflow.md)**: Understand the LangGraph workflow layout.
* **[Available MCP Servers & Tooling](docs/available_mcp_tools.md)**: Discover all integrated tool definitions.
* **[LangGraph Migration & Prototype](docs/langgraph_migration.md)**: Read the backstory and transition details for the underlying orchestration layer.
