from ai.base_analyst import BaseAnalyst


class RepositoryAnalyst(BaseAnalyst):
    """Model 1: repository structure, commits, code changes, architecture, feature tracking, evolution."""
    role = "repository_analyst"
    kinds = {"commit", "pr", "file_tree", "branch", "repo", "contributor", "review"}
