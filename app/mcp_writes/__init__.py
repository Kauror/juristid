"""Writes from an AI client: prepare, approve in Juristid, commit (docs/adr/0189).

`operations` are the typed changes an MCP client may *propose*, each over the
canonical services the pages call. A proposal is stored, shown to its person
on Juristid's own approval page, approved there with their personal
authenticator, and only then executed by the write gateway (`mcp-writer`).
The `mcp` process never writes; it forwards the client's request.
"""
