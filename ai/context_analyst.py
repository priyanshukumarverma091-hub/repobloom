from ai.base_analyst import BaseAnalyst


class ContextAnalyst(BaseAnalyst):
    """Model 2: issues, Discord discussions, bug investigations, decisions, problem->solution links."""
    role = "context_analyst"
    kinds = {"issue", "message", "doc", "review", "pr"}
