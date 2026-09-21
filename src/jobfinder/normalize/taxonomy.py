"""Field taxonomy and skill vocabulary.

Delivers the "fields I want, and fields related to those" requirement: a user asking for
Data Engineering should also see analytics engineering, ETL, data platform and BI roles
without anyone hand-maintaining a synonym list per user.

Each field carries three things:

* ``terms``    - words that indicate a job belongs to the field
* ``related``  - neighbouring fields, expanded one hop out from the user's selection
* ``skills``   - concrete technologies used to score a resume against a posting

The EU's ESCO classification is the eventual home for this (it is free, multilingual and
the standard across the Irish and wider EU labour market). This curated table is the
pragmatic stand-in: it ships with no download step and covers the sectors that actually
dominate Dublin hiring - technology, financial services, pharma and medtech.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    terms: tuple[str, ...]
    related: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


FIELDS: dict[str, Field] = {}


def _add(field_obj: Field) -> None:
    FIELDS[field_obj.key] = field_obj


_add(Field(
    key="software-engineering",
    label="Software Engineering",
    # "software dev" and "sde" matter more than they look: Amazon titles thousands of
    # roles "Software Dev Engineer" / "SDE II", none of which contain the exact string
    # "software engineer", so without these they fail to classify at all.
    terms=("software engineer", "developer", "programmer", "swe", "full stack",
           "backend", "back end", "frontend", "front end", "engineer ii",
           "software development", "software dev", "sde", "dev engineer",
           "engineering manager", "systems engineer", "applications engineer"),
    related=("backend", "frontend", "devops", "mobile", "qa", "data-engineering",
             "security-engineering", "engineering-management"),
    skills=("python", "java", "javascript", "typescript", "go", "golang", "rust",
            "c++", "c#", ".net", "ruby", "scala", "kotlin", "php", "node.js",
            "react", "angular", "vue", "django", "flask", "spring", "fastapi"),
))
_add(Field(
    key="backend",
    label="Backend Engineering",
    terms=("backend", "back end", "server side", "api engineer", "platform engineer",
           "distributed systems"),
    related=("software-engineering", "devops", "data-engineering", "cloud"),
    skills=("python", "java", "go", "rust", "microservices", "grpc", "rest",
            "postgresql", "mysql", "redis", "kafka", "rabbitmq"),
))
_add(Field(
    key="frontend",
    label="Frontend Engineering",
    terms=("frontend", "front end", "ui engineer", "web developer", "javascript engineer"),
    related=("software-engineering", "mobile", "design"),
    skills=("javascript", "typescript", "react", "angular", "vue", "svelte", "css",
            "html", "next.js", "webpack", "tailwind"),
))
_add(Field(
    key="mobile",
    label="Mobile Engineering",
    terms=("mobile engineer", "ios", "android", "react native", "flutter"),
    related=("software-engineering", "frontend"),
    skills=("swift", "objective-c", "kotlin", "java", "react native", "flutter", "dart"),
))
_add(Field(
    key="data-engineering",
    label="Data Engineering",
    terms=("data engineer", "etl", "elt", "data platform", "analytics engineer",
           "data pipeline", "data warehouse", "big data"),
    related=("data-science", "backend", "cloud", "business-intelligence", "machine-learning"),
    skills=("sql", "python", "spark", "airflow", "dbt", "snowflake", "databricks",
            "kafka", "hadoop", "bigquery", "redshift", "scala"),
))
_add(Field(
    key="data-science",
    label="Data Science",
    terms=("data scientist", "data science", "statistician", "quantitative analyst",
           "research scientist"),
    related=("machine-learning", "data-engineering", "business-intelligence"),
    skills=("python", "r", "sql", "pandas", "numpy", "scikit-learn", "statistics",
            "jupyter", "matplotlib", "tensorflow", "pytorch"),
))
_add(Field(
    key="machine-learning",
    label="Machine Learning & AI",
    terms=("machine learning", "ml engineer", "ai engineer", "deep learning", "nlp",
           "computer vision", "mlops", "artificial intelligence", "llm"),
    related=("data-science", "data-engineering", "software-engineering"),
    skills=("pytorch", "tensorflow", "scikit-learn", "hugging face", "transformers",
            "keras", "mlflow", "cuda", "python", "llm", "rag"),
))
_add(Field(
    key="business-intelligence",
    label="Business Intelligence & Analytics",
    terms=("business intelligence", "bi analyst", "data analyst", "analytics",
           "reporting analyst", "insights analyst"),
    related=("data-science", "data-engineering", "finance"),
    skills=("sql", "tableau", "power bi", "looker", "excel", "dbt", "qlik", "python"),
))
_add(Field(
    key="devops",
    label="DevOps & SRE",
    terms=("devops", "site reliability", "sre", "platform engineer", "infrastructure engineer",
           "build engineer", "release engineer"),
    related=("cloud", "backend", "security-engineering", "software-engineering"),
    skills=("kubernetes", "docker", "terraform", "ansible", "jenkins", "gitlab",
            "github actions", "prometheus", "grafana", "linux", "bash", "aws",
            "azure", "gcp", "helm", "argocd"),
))
_add(Field(
    key="cloud",
    label="Cloud Engineering",
    terms=("cloud engineer", "cloud architect", "aws", "azure", "gcp", "solutions architect"),
    related=("devops", "backend", "security-engineering"),
    skills=("aws", "azure", "gcp", "terraform", "kubernetes", "serverless",
            "lambda", "cloudformation", "docker"),
))
_add(Field(
    key="security-engineering",
    label="Security & Cybersecurity",
    terms=("security engineer", "cybersecurity", "infosec", "application security",
           "penetration test", "security analyst", "soc analyst", "grc"),
    related=("devops", "cloud", "backend", "compliance"),
    skills=("siem", "splunk", "burp", "nmap", "owasp", "iso 27001", "soc 2",
            "penetration testing", "cryptography", "zero trust"),
))
_add(Field(
    key="qa",
    label="QA & Test Engineering",
    terms=("qa engineer", "quality assurance", "test engineer", "sdet",
           "automation engineer", "quality engineer"),
    related=("software-engineering", "devops"),
    skills=("selenium", "cypress", "playwright", "pytest", "junit", "testng",
            "appium", "postman", "jmeter"),
))
_add(Field(
    key="engineering-management",
    label="Engineering Management",
    terms=("engineering manager", "director of engineering", "vp engineering",
           "head of engineering", "technical lead", "team lead", "staff engineer",
           "principal engineer"),
    related=("software-engineering", "product-management"),
    skills=("leadership", "agile", "scrum", "hiring", "mentoring", "roadmap"),
))
_add(Field(
    key="product-management",
    label="Product Management",
    terms=("product manager", "product owner", "product lead", "head of product",
           "technical product"),
    related=("engineering-management", "design", "business-intelligence", "marketing"),
    skills=("roadmap", "agile", "scrum", "jira", "user research", "a/b testing",
            "stakeholder management", "okrs"),
))
_add(Field(
    key="design",
    label="Design & UX",
    terms=("designer", "ux", "ui designer", "product design", "user experience",
           "interaction design", "graphic design"),
    related=("product-management", "frontend"),
    skills=("figma", "sketch", "adobe xd", "prototyping", "user research",
            "design systems", "wireframing", "accessibility"),
))
_add(Field(
    key="finance",
    label="Finance & Accounting",
    terms=("accountant", "financial analyst", "finance manager", "controller",
           "fp&a", "treasury", "audit", "bookkeeper", "tax"),
    related=("business-intelligence", "compliance", "operations"),
    skills=("acca", "aca", "cima", "cpa", "ifrs", "gaap", "sap", "oracle financials",
            "excel", "netsuite", "financial modelling"),
))
_add(Field(
    key="compliance",
    label="Risk, Legal & Compliance",
    terms=("compliance", "risk manager", "regulatory", "legal counsel", "aml",
           "kyc", "governance", "data protection", "solicitor"),
    related=("finance", "security-engineering"),
    skills=("aml", "kyc", "gdpr", "mifid", "solvency ii", "basel", "risk assessment"),
))
_add(Field(
    key="sales",
    label="Sales & Business Development",
    terms=("account executive", "sales", "business development", "sdr", "bdr",
           "account manager", "customer success", "partnerships", "solutions consultant"),
    related=("marketing", "operations"),
    skills=("salesforce", "hubspot", "crm", "outreach", "negotiation", "saas",
            "pipeline management", "b2b"),
))
_add(Field(
    key="marketing",
    label="Marketing & Communications",
    terms=("marketing", "growth", "seo", "content", "brand", "communications",
           "demand generation", "campaign"),
    related=("sales", "design", "product-management"),
    skills=("seo", "sem", "google analytics", "hubspot", "marketo", "content strategy",
            "social media", "copywriting", "email marketing"),
))
_add(Field(
    key="operations",
    label="Operations & Supply Chain",
    terms=("operations", "supply chain", "logistics", "procurement", "warehouse",
           "programme manager", "project manager", "business analyst"),
    related=("finance", "sales", "manufacturing"),
    skills=("sap", "lean", "six sigma", "erp", "prince2", "pmp", "agile", "jira"),
))
_add(Field(
    key="hr",
    label="HR & Talent",
    terms=("recruiter", "talent acquisition", "human resources", "hr business partner",
           "people operations", "learning and development"),
    related=("operations",),
    skills=("workday", "greenhouse", "ats", "employee relations", "cipd", "onboarding"),
))
_add(Field(
    key="pharma",
    label="Pharma, Biotech & MedTech",
    terms=("pharmaceutical", "biotech", "clinical", "regulatory affairs", "qa validation",
           "manufacturing science", "medical device", "gmp", "laboratory"),
    related=("compliance", "manufacturing", "operations"),
    skills=("gmp", "gxp", "hplc", "validation", "cleanroom", "iso 13485",
            "clinical trials", "quality control"),
))
_add(Field(
    key="manufacturing",
    label="Manufacturing & Engineering",
    terms=("mechanical engineer", "electrical engineer", "process engineer",
           "manufacturing engineer", "maintenance", "automation engineer",
           "civil engineer", "quantity surveyor"),
    related=("operations", "pharma"),
    skills=("autocad", "solidworks", "plc", "scada", "lean", "six sigma", "cnc"),
))
_add(Field(
    key="customer-support",
    label="Customer Support",
    terms=("customer support", "customer service", "technical support", "help desk",
           "service desk", "trust and safety", "content moderator"),
    related=("sales", "operations"),
    skills=("zendesk", "salesforce", "intercom", "jira service", "troubleshooting"),
))

# Every skill token known to the taxonomy, for resume extraction.
ALL_SKILLS: frozenset[str] = frozenset(
    skill for f in FIELDS.values() for skill in f.skills
)


def expand_fields(keys: list[str] | tuple[str, ...]) -> list[str]:
    """Expand chosen fields one hop into their related neighbours.

    This is the "and related fields" half of the requirement. One hop is deliberate:
    two hops tends to reach the whole graph and stops meaning anything.
    """
    expanded: list[str] = []
    for key in keys:
        field_obj = FIELDS.get(key)
        if not field_obj:
            continue
        if key not in expanded:
            expanded.append(key)
        for neighbour in field_obj.related:
            if neighbour in FIELDS and neighbour not in expanded:
                expanded.append(neighbour)
    return expanded


def terms_for(keys: list[str] | tuple[str, ...]) -> set[str]:
    return {term for key in keys if key in FIELDS for term in FIELDS[key].terms}


def skills_for(keys: list[str] | tuple[str, ...]) -> set[str]:
    return {skill for key in keys if key in FIELDS for skill in FIELDS[key].skills}


# A term must begin at a word boundary, but need not end at one. The asymmetry is the
# point: "software dev" has to keep matching "Software Development" and "product design"
# has to keep matching "Product Designer", so the right-hand side stays open for
# inflections. Leaving the left side open too made this a raw substring test, which read
# "ux" out of "(Benelux)", "sales" out of "Presales", "ios" out of "Studios" and "aws"
# out of "Laws" - filing those roles under fields they have nothing to do with.
_FIELD_TERMS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    key: [
        (term, re.compile(r"(?<![a-z0-9])" + re.escape(term)))
        for term in field_obj.terms
    ]
    for key, field_obj in FIELDS.items()
}


def classify_title(title: str) -> list[str]:
    """Guess which fields a job title belongs to."""
    if not title:
        return []
    lowered = f" {title.lower()} "
    # The substring test first, for the same reason as extract_skills: this runs for
    # every candidate job on every search, and a regex cannot skip ahead the way a plain
    # scan does. It never changes the outcome - every term is lowercase ASCII, so a
    # pattern can only match where the bare substring is already present.
    return [
        key
        for key, terms in _FIELD_TERMS.items()
        if any(term in lowered and pattern.search(lowered) for term, pattern in terms)
    ]


_SKILL_PATTERNS = {
    skill: (
        skill.lower(),
        re.compile(rf"(?<![\w+#.]){re.escape(skill)}(?![\w+#])", re.IGNORECASE),
    )
    for skill in ALL_SKILLS
}


def extract_skills(text: str) -> set[str]:
    """Find known skill tokens in free text.

    Word-boundary matching that tolerates the punctuation in real technology names -
    "C++", "C#", ".NET", "Node.js" - which a plain ``\\b`` boundary silently misses.

    Every search runs this over every candidate job's full description, and a regex with
    a lookbehind cannot skip ahead the way a plain substring scan does: 188 patterns over
    ~1,500 descriptions was 10 of a search's 13 seconds. So each pattern only runs when
    its skill appears in the text at all. That test cannot change the result: every
    skill is ASCII, so a case-insensitive match of the escaped literal implies the
    lowercased skill is a substring of the lowercased text.
    """
    if not text:
        return set()
    lowered = text.lower()
    return {
        skill
        for skill, (needle, pattern) in _SKILL_PATTERNS.items()
        if needle in lowered and pattern.search(text)
    }
