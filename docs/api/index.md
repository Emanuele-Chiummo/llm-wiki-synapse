# API Reference

The Synapse API is defined as an OpenAPI 3.0.0 specification, auto-generated from the FastAPI application.

## OpenAPI Specification

The complete API specification is available as JSON:

**File:** [`openapi.json`](openapi.json)

This specification includes:

- **REST endpoints** — pages, graph, chat, ingest, configuration
- **WebSocket upgrade paths** — streaming chat
- **Request/response schemas** — Pydantic models
- **Security schemes** — Bearer token authentication (v1.0+)
- **Health & status endpoints** — monitoring and readiness checks

## Exploring the API

You can view and test the API using:

1. **Built-in Swagger UI** — typically available at `http://your-backend:8000/docs`
2. **ReDoc** — typically available at `http://your-backend:8000/redoc`
3. **OpenAPI clients** — generate code from `openapi.json` using tools like:
   - [OpenAPI Generator](https://openapi-generator.tech/)
   - [Swagger Codegen](https://swagger.io/tools/swagger-codegen/)

## FastMCP Server

In addition to the REST API, Synapse can optionally expose an MCP (Model Context Protocol) server for AI agents to access vault files and operations. See [ADR-0029 (Remote MCP over HTTP)](../adr/0029-remote-mcp-over-http.md) and [ADR-0033 (UI-settable MCP token)](../adr/0033-ui-settable-mcp-token-allow-without-token.md) for details.

The MCP tools schema is available separately in [`mcp-tools.json`](mcp-tools.json).

---

**Note:** The OpenAPI specification is regenerated and committed during each sprint. It is the source of truth for the API contract.
