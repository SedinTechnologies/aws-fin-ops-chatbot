# DevOps AI Initiatives

## About DevOps AI Projects

This repository hosts a collection of projects focused on leveraging Artificial Intelligence (AI) and Large Language Models (LLMs) to revolutionize DevOps workflows. Our goal is to automate complex tasks, optimize resource management, enhance observability, and streamline FinOps practices using cutting-edge AI technologies.

## Projects

### 1. AWS FinOps Chatbot

**[AWS FinOps Bot](./aws-fin-ops-chatbot/README.md)** is an AI-driven assistant designed to analyze AWS billing, cost optimization, and resource usage. It empowers users to interact with their AWS cost data using natural language.

*   **Key Features**:
    *   **Cost Analytics**: Analyze monthly spend, break down costs by service/region, and detect anomalies.
    *   **Resource Insights**: Identify unused or underutilized resources via Cloud Control API.
    *   **Secure & Private**: Strict domain-bound responses ensuring conversations stay within AWS FinOps topics.
    *   **Interactive UI**: Built with Chainlit for a seamless chat experience.

*   **Technology Stack**:
    *   **LLM Engine**: Azure OpenAI
    *   **UI**: Chainlit
    *   **Data Retrieval**: MCP Servers (Model Context Protocol)
    *   **Storage**: PostgreSQL & Redis
    *   **Dev Tools**: Localstack for offline development

[Read more about AWS FinOps Chatbot](./aws-fin-ops-chatbot/README.md)
