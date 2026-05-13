"""Central, identity-free test constants.

We use generic names (alice / acme) for fixtures so:
  1. Anyone forking the repo for open-source use sees neutral data
     in tests, not the original maintainer's name / company.
  2. Test failures don't accidentally leak personal context (org
     names, internal repo paths) into stack traces / CI logs.
  3. New tests have one obvious place to import from instead of
     re-typing literals.

A test that's specifically asserting "the hardcoded production
constant resolves correctly" should still reference
`adapters.github.ALLOWED_REPOS` (etc.) directly so the assertion
tracks production. Pure fixtures (the value doesn't matter, we just
need *some* repo / login) should import from here.
"""

# ---- Identities ----

# Primary user login: stand-in for the OSS-fork-account a typical
# developer uses (e.g. their personal GitHub).
TEST_USER_LOGIN = "alice"
# Secondary login: the company / "data" account you'd PR with from a
# work fork.
TEST_USER_LOGIN_ALT = "alice-work"
# Display name (free-text).
TEST_USER_DISPLAY_NAME = "Alice Tester"
TEST_USER_EMAIL = "alice@example.com"


# ---- Repos ----

# Generic OSS upstream (no real org behind "acme/widgets").
TEST_OSS_REPO = "acme/widgets"
# Personal fork of the OSS repo.
TEST_OSS_FORK = f"{TEST_USER_LOGIN}/widgets"

# Generic company namespace + a couple sub-repos. Wildcards in tests
# use `TEST_COMPANY_ORG + "/*"`.
TEST_COMPANY_ORG = "acme-corp"
TEST_COMPANY_REPO_RUNTIME = f"{TEST_COMPANY_ORG}/runtime"
TEST_COMPANY_REPO_PLATFORM = f"{TEST_COMPANY_ORG}/platform"
TEST_COMPANY_FORK = f"{TEST_USER_LOGIN_ALT}/runtime"


# ---- Tickets / JIRA ----

TEST_TICKET_PREFIX = "ACME"
TEST_TICKET_KEY = f"{TEST_TICKET_PREFIX}-123"
TEST_JIRA_BASE = "https://acme.atlassian.net"


# ---- Helpers ----

def pr_url(repo: str = TEST_OSS_REPO, number: int = 1) -> str:
    """Build a github.com PR URL using only generic names."""
    return f"https://github.com/{repo}/pull/{number}"
