# Available MCP Tools

The AWS FinOps Chatbot integrates with the following Model Context Protocol (MCP) servers to provide comprehensive AWS management capabilities.

## 1. AWS API MCP Server

* **Package**: `aws-api-mcp-server`
* **Port**: 8000
* **Purpose**: General-purpose direct interaction with any AWS service API.
* **Key Capabilities**: Reading resource configurations, executing operational commands, and querying service-specific data across the AWS ecosystem (e.g., EC2, S3, Lambda, ECS, CloudWatch).

## 2. AWS Documentation MCP Server

* **Package**: `aws-documentation-mcp-server`
* **Port**: 8001
* **Purpose**: Retrieves the most up-to-date AWS service documentation, limits, and best practices.

## 3. AWS Pricing MCP Server

* **Package**: `aws-pricing-mcp-server`
* **Port**: 8002
* **Purpose**: Access to AWS Price List API for retrieving service pricing information and cost comparisons.

## 4. AWS Billing & Cost Management MCP Server

* **Package**: `aws-billing-cost-management-mcp-server`
* **Port**: 8003
* **Purpose**: Access to billing data, Cost Explorer insights, invoices, budgets, and savings plans.

## 5. AWS CloudTrail MCP Server

* **Package**: `aws-cloudtrail-mcp-server`
* **Port**: 8004
* **Purpose**: Access to CloudTrail events for auditing, user activity, and security analysis.

## 6. AWS IaC MCP Server

* **Package**: `aws-iac-mcp-server`
* **Port**: 8005
* **Purpose**: Provides detailed Infrastructure as Code insights.
