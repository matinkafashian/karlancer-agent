"""Central selectors grounded in a real Karlancer project page."""

PROJECT_ROOT = "app-project-card[type='project-bids']"
PROJECT_TITLE = f"{PROJECT_ROOT} h1"
PROJECT_DESCRIPTION = f"{PROJECT_ROOT} .white-space-pre-line"
PROJECT_SKILLS = f"{PROJECT_ROOT} h3 a[href*='/search?q=']"
SUBMIT_CONTROL = f"{PROJECT_ROOT} [id^='setBidBtn']"
CANONICAL_LINK = "link[rel='canonical']"
