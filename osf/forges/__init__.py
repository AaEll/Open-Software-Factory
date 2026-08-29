"""Real forge adapters implementing `osf.forge.Forge`.

`osf.local.forge.InMemoryForge` is the offline reference; `github.GitHubForge` talks to the GitHub
REST API so the reconcile loop provisions real repos, PRs, checks, and merges. Needs the `github`
extra (`pip install -e ".[github]"`) and a `GITHUB_TOKEN`/`GH_TOKEN`.
"""
