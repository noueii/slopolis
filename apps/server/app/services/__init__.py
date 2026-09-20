"""Domain services that orchestrate adapters and persistence.

Each service owns one workflow the routers call into — for example recording a
GitHub App installation and its repositories — so the HTTP layer stays a thin
translation between the wire contract and these operations.
"""
