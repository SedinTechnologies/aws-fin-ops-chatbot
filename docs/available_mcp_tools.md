# Available MCP Tools

The AWS FinOps Chatbot integrates with the following Model Context Protocol (MCP) servers to provide comprehensive AWS management capabilities.

## 1. AWS Cost Explorer MCP Server
*   **Package**: `awslabs.cost-explorer-mcp-server`
*   **Port**: 8001
*   **Purpose**: Provides access to AWS Cost Explorer API for analyzing cost and usage data.
*   **Key Tools**: `get_cost_and_usage`, `get_dimension_values`.

## 2. AWS Cloud Control API (CCAPI) MCP Server
*   **Package**: `awslabs.ccapi-mcp-server`
*   **Port**: 8002
*   **Purpose**: Enables management of AWS resources using the Cloud Control API standard.
*   **Key Tools**: `get_resource`, `list_resources`, `update_resource`.

## 3. AWS CloudWatch MCP Server
*   **Package**: `awslabs.cloudwatch-mcp-server`
*   **Port**: 8003
*   **Purpose**: Access to CloudWatch metrics and logs for monitoring resource performance.
*   **Key Tools**: `get_metric_data`, `list_metrics`.

## 4. AWS Billing & Cost Management MCP Server
*   **Package**: `awslabs.billing-cost-management-mcp-server`
*   **Port**: 8004
*   **Purpose**: Access to billing data, including invoices and budget information.
*   **Key Tools**: `list_bill_estimates`, `get_budget_details`.

## 5. AWS CloudTrail MCP Server
*   **Package**: `awslabs.cloudtrail-mcp-server`
*   **Port**: 8005
*   **Purpose**: Access to CloudTrail events for auditing and security analysis.
*   **Key Tools**: `lookup_events`.

## 6. AWS Pricing MCP Server
*   **Package**: `awslabs.aws-pricing-mcp-server`
*   **Port**: 8006
*   **Purpose**: Access to AWS Price List API for retrieving service pricing information.
*   **Key Tools**: `get_products`, `get_attribute_values`.
