"""Safe, synthetic domain fixtures for classifier and DNS-proxy demos.

All entries use the reserved .test top-level domain. They do not resolve to
public websites, and are not a verified classification of real-world sites.
"""

CATEGORY_COUNTS = {
    "education": (34, "edu-fixture"),
    "entertainment": (34, "fun-fixture"),
    "social": (33, "social-fixture"),
    "games": (33, "game-fixture"),
    "age_inappropriate": (33, "restricted-fixture"),
    "unclassified": (33, "unknown-fixture"),
}

DOMAIN_CATALOG: dict[str, str] = {}
for category, (count, prefix) in CATEGORY_COUNTS.items():
    for number in range(1, count + 1):
        DOMAIN_CATALOG[f"{prefix}-{number:03d}.test"] = category

assert len(DOMAIN_CATALOG) == 200
CATEGORIES = tuple(CATEGORY_COUNTS)
PERMANENT_ALLOW_DOMAINS = (
    "111.vn", "example.org", "example.net", "example.edu",
    *(domain for domain, category in DOMAIN_CATALOG.items() if category == "education"),
)


def root_domain(name: str) -> str:
    """Return last two labels; a simple approximation, not a public-suffix parser."""
    labels = name.strip(".").lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else name.strip(".").lower()


def matches_domain(name: str, rules: list[str]) -> str | None:
    candidate = name.strip(".").lower()
    for rule in rules:
        normalized = rule.strip(".").lower()
        if candidate == normalized or candidate.endswith("." + normalized):
            return normalized
    return None


def category_for(name: str) -> str:
    candidate = name.strip(".").lower()
    if candidate in DOMAIN_CATALOG:
        return DOMAIN_CATALOG[candidate]
    for fixture, category in DOMAIN_CATALOG.items():
        if candidate.endswith("." + fixture):
            return category
    return "unclassified"
