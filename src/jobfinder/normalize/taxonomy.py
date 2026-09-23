"""Field taxonomy and skill vocabulary.

Delivers the "fields I want, and fields related to those" requirement: a user asking for
Data Engineering should also see analytics engineering, ETL, data platform and BI roles
without anyone hand-maintaining a synonym list per user.

Two levels. A handful of top-level ``GROUPS`` - Engineering & Technology, Data & AI,
Finance & Legal and so on - each hold the fields a searcher actually picks from, so the
picker reads as a contents page rather than a wall of checkboxes.

Each field carries four things:

* ``group``    - the heading it files under
* ``terms``    - words that indicate a job belongs to the field
* ``related``  - neighbouring fields, expanded one hop out from the user's selection,
                 each weighted by how close it really is (see ``NEAR`` / ``FAR``)
* ``skills``   - concrete technologies used to score a resume against a posting

The EU's ESCO classification is the eventual home for this (it is free, multilingual and
the standard across the Irish and wider EU labour market). This curated table is the
pragmatic stand-in: it ships with no download step and covers the sectors that actually
dominate Dublin hiring - technology, financial services, pharma and medtech.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field


# How close a neighbouring field really is. NEAR is the same craft under another name -
# a Data Scientist reads Machine Learning adverts and recognises the job. FAR is a
# plausible pivot, not the same work.
#
# The distinction is not cosmetic. Software Engineering is by far the largest bucket in
# the Dublin corpus and its terms are deliberately loose ("developer", "engineer ii",
# "sde"), so an unweighted edge into it drowns whatever the searcher actually ticked:
# picking Machine Learning returned a page of "Software Developer Graduate".
NEAR = 1.0
FAR = 0.4


def _near(*keys: str) -> tuple[tuple[str, float], ...]:
    return tuple((key, NEAR) for key in keys)


def _far(*keys: str) -> tuple[tuple[str, float], ...]:
    return tuple((key, FAR) for key in keys)


@dataclass(frozen=True)
class Field:
    key: str
    group: str
    label: str
    terms: tuple[str, ...]
    related: tuple[tuple[str, float], ...] = ()
    skills: tuple[str, ...] = ()


FIELDS: dict[str, Field] = {}


def _add(field_obj: Field) -> None:
    FIELDS[field_obj.key] = field_obj


# Top-level groups, in the order the picker shows them. Every field belongs to exactly
# one, so a searcher scans eleven headings rather than fifty-odd checkboxes and can see
# at a glance that Machine Learning sits beside Data Science, not beside Backend.
GROUPS: tuple[tuple[str, str], ...] = (
    ("engineering", "Engineering & Technology"),
    ("data-ai", "Data & AI"),
    ("product-design", "Product & Design"),
    ("science-health", "Science & Healthcare"),
    ("industry", "Industrial & Built Environment"),
    ("finance-legal", "Finance & Legal"),
    ("commercial", "Sales & Marketing"),
    ("business-ops", "Business & Operations"),
    ("people", "People & Talent"),
    ("public-education", "Education & Public Service"),
    ("service", "Hospitality, Retail & Transport"),
)

GROUP_LABELS: dict[str, str] = dict(GROUPS)


# ---------------------------------------------------------------------------
# Engineering & Technology
# ---------------------------------------------------------------------------

_add(Field(
    key="software-engineering",
    group="engineering",
    label="Software Engineering",
    # "software dev" and "sde" matter more than they look: Amazon titles thousands of
    # roles "Software Dev Engineer" / "SDE II", none of which contain the exact string
    # "software engineer", so without these they fail to classify at all.
    terms=("software engineer", "developer", "programmer", "swe", "full stack",
           "backend", "back end", "frontend", "front end", "engineer ii",
           "software development", "software dev", "sde", "dev engineer",
           "applications engineer", "api engineer"),
    related=_near("backend", "frontend", "mobile", "engineering-management")
            + _far("devops", "qa", "embedded", "data-engineering", "security-engineering"),
    skills=("python", "java", "javascript", "typescript", "go", "golang", "rust",
            "c++", "c#", ".net", "ruby", "scala", "kotlin", "php", "node.js",
            "react", "angular", "vue", "django", "flask", "spring", "fastapi",
            "git", "rest", "graphql", "microservices", "docker", "sql", "linux"),
))
_add(Field(
    key="backend",
    group="engineering",
    label="Backend Engineering",
    terms=("backend", "back end", "server side", "api engineer", "platform engineer",
           "distributed systems"),
    related=_near("software-engineering") + _far("devops", "data-engineering", "cloud"),
    skills=("python", "java", "go", "rust", "microservices", "grpc", "rest",
            "postgresql", "mysql", "redis", "kafka", "rabbitmq", "graphql",
            "elasticsearch", "mongodb", "spring boot", "celery"),
))
_add(Field(
    key="frontend",
    group="engineering",
    label="Frontend Engineering",
    terms=("frontend", "front end", "ui engineer", "web developer", "javascript engineer",
           "web engineer"),
    related=_near("software-engineering", "mobile") + _far("design"),
    skills=("javascript", "typescript", "react", "angular", "vue", "svelte", "css",
            "html", "next.js", "webpack", "tailwind", "sass", "redux", "vite",
            "accessibility", "storybook"),
))
_add(Field(
    key="mobile",
    group="engineering",
    label="Mobile Engineering",
    terms=("mobile engineer", "mobile developer", "ios", "android", "react native",
           "flutter"),
    related=_near("software-engineering", "frontend"),
    skills=("swift", "swiftui", "objective-c", "kotlin", "java", "react native",
            "flutter", "dart", "jetpack compose", "xcode", "gradle"),
))
_add(Field(
    key="embedded",
    group="engineering",
    label="Embedded & Firmware",
    terms=("embedded", "firmware", "rtos", "device driver", "bare metal",
           "silicon engineer", "fpga", "hardware engineer"),
    related=_near("electrical-engineering") + _far("software-engineering", "manufacturing"),
    skills=("c++", "rust", "assembly", "rtos", "freertos", "zephyr", "arm",
            "yocto", "verilog", "vhdl", "can bus", "i2c", "spi", "oscilloscope"),
))
_add(Field(
    key="game-development",
    group="engineering",
    label="Game Development",
    terms=("game developer", "game engineer", "gameplay", "game designer",
           "unreal engine", "technical artist"),
    related=_near("software-engineering") + _far("design", "mobile"),
    skills=("unity", "unreal engine", "c++", "c#", "opengl", "vulkan", "blender",
            "shader", "physics engine", "3d modelling"),
))
_add(Field(
    key="devops",
    group="engineering",
    label="DevOps & SRE",
    terms=("devops", "site reliability", "sre", "platform engineer",
           "infrastructure engineer", "build engineer", "release engineer",
           "systems engineer"),
    related=_near("cloud")
            + _far("backend", "security-engineering", "software-engineering", "it-support"),
    skills=("kubernetes", "docker", "terraform", "ansible", "jenkins", "gitlab",
            "github actions", "prometheus", "grafana", "linux", "bash", "aws",
            "azure", "gcp", "helm", "argocd", "datadog", "pagerduty", "ci/cd",
            "observability", "pulumi"),
))
_add(Field(
    key="cloud",
    group="engineering",
    label="Cloud Engineering & Architecture",
    terms=("cloud engineer", "cloud architect", "aws", "azure", "gcp",
           "solutions architect", "cloud consultant"),
    related=_near("devops") + _far("backend", "security-engineering", "network-engineering"),
    skills=("aws", "azure", "gcp", "terraform", "kubernetes", "serverless",
            "lambda", "cloudformation", "docker", "iam", "vpc", "well-architected",
            "finops"),
))
_add(Field(
    key="network-engineering",
    group="engineering",
    label="Networks & Telecoms",
    terms=("network engineer", "network architect", "telecom", "voip",
           "noc engineer", "wireless engineer", "rf engineer", "datacenter"),
    related=_near("it-support", "cloud") + _far("devops", "security-engineering"),
    skills=("cisco", "ccna", "ccnp", "bgp", "ospf", "mpls", "juniper", "tcp/ip",
            "dns", "vpn", "sd-wan", "firewall", "wireshark", "5g", "lte"),
))
_add(Field(
    key="security-engineering",
    group="engineering",
    label="Security & Cybersecurity",
    terms=("security engineer", "cybersecurity", "infosec", "application security",
           "penetration test", "security analyst", "soc analyst", "security architect",
           "threat intelligence", "incident response"),
    related=_near("devops", "cloud") + _far("network-engineering", "compliance", "backend"),
    skills=("siem", "splunk", "burp", "nmap", "owasp", "iso 27001", "soc 2",
            "penetration testing", "crowdstrike", "kali", "metasploit", "nessus",
            "zero trust", "cissp", "mitre att&ck", "edr", "threat modelling"),
))
_add(Field(
    key="qa",
    group="engineering",
    label="QA & Test Engineering",
    terms=("qa engineer", "quality assurance", "test engineer", "sdet",
           "automation tester", "test analyst", "quality engineer"),
    related=_near("software-engineering") + _far("devops"),
    skills=("selenium", "cypress", "playwright", "pytest", "junit", "testng",
            "postman", "jmeter", "appium", "test automation", "bdd", "cucumber"),
))
_add(Field(
    key="it-support",
    group="engineering",
    label="IT Support & Systems Administration",
    terms=("it support", "service desk", "help desk", "helpdesk", "desktop support",
           "systems administrator", "sysadmin", "it technician", "it analyst",
           "end user computing"),
    related=_near("network-engineering") + _far("devops", "customer-support",
                                                "security-engineering"),
    skills=("active directory", "windows server", "office 365", "intune", "jamf",
            "servicenow", "itil", "powershell", "vmware", "citrix", "sccm",
            "troubleshooting", "zendesk"),
))
_add(Field(
    key="engineering-management",
    group="engineering",
    label="Engineering Management",
    terms=("engineering manager", "director of engineering", "vp engineering",
           "head of engineering", "technical lead", "tech lead", "cto"),
    related=_near("software-engineering") + _far("product-management", "project-management"),
    skills=("agile", "scrum", "hiring", "mentoring", "roadmap", "okrs",
            "stakeholder management", "budgeting"),
))

# ---------------------------------------------------------------------------
# Data & AI
# ---------------------------------------------------------------------------

_add(Field(
    key="machine-learning",
    group="data-ai",
    label="Machine Learning & AI",
    terms=("machine learning", "ml engineer", "ai engineer", "deep learning", "nlp",
           "computer vision", "mlops", "artificial intelligence", "llm",
           "applied scientist", "generative ai", "ml scientist", "prompt engineer"),
    related=_near("data-science", "data-engineering", "research-science")
            + _far("software-engineering"),
    skills=("pytorch", "tensorflow", "scikit-learn", "hugging face", "transformers",
            "keras", "mlflow", "cuda", "python", "llm", "rag", "langchain",
            "vector database", "fine-tuning", "onnx", "sagemaker", "vertex ai",
            "computer vision", "nlp", "numpy", "pandas", "weights & biases",
            "kubeflow", "diffusion", "reinforcement learning"),
))
_add(Field(
    key="data-science",
    group="data-ai",
    label="Data Science",
    terms=("data scientist", "data science", "statistician", "decision scientist",
           "research scientist"),
    related=_near("machine-learning", "data-engineering", "business-intelligence",
                  "research-science"),
    skills=("python", "r", "sql", "pandas", "numpy", "scikit-learn", "statistics",
            "jupyter", "matplotlib", "tensorflow", "pytorch", "a/b testing",
            "experimentation", "causal inference", "bayesian", "forecasting",
            "regression", "clustering", "seaborn", "databricks"),
))
_add(Field(
    key="data-engineering",
    group="data-ai",
    label="Data Engineering",
    terms=("data engineer", "etl", "elt", "data platform", "analytics engineer",
           "data pipeline", "data warehouse", "big data", "data architect"),
    related=_near("data-science", "business-intelligence", "machine-learning")
            + _far("backend", "cloud"),
    skills=("sql", "python", "spark", "airflow", "dbt", "snowflake", "databricks",
            "kafka", "hadoop", "bigquery", "redshift", "scala", "flink", "delta lake",
            "data modelling", "fivetran", "glue", "synapse", "parquet"),
))
_add(Field(
    key="business-intelligence",
    group="data-ai",
    label="Business Intelligence & Analytics",
    terms=("business intelligence", "bi analyst", "data analyst", "analytics",
           "reporting analyst", "insights analyst", "bi developer",
           "performance analyst"),
    related=_near("data-science", "data-engineering", "business-analysis")
            + _far("finance"),
    skills=("sql", "tableau", "power bi", "looker", "excel", "dbt", "qlik", "python",
            "dax", "google analytics", "data studio", "vba", "sheets", "cohort analysis"),
))
_add(Field(
    key="research-science",
    group="data-ai",
    label="Research & Applied Science",
    terms=("research scientist", "research engineer", "postdoctoral", "postdoc",
           "research fellow", "research associate", "phd researcher"),
    related=_near("machine-learning", "data-science") + _far("laboratory", "education"),
    skills=("python", "matlab", "r", "latex", "simulation", "publications",
            "experimental design", "statistics", "hpc", "c++"),
))
_add(Field(
    key="quantitative-finance",
    group="data-ai",
    label="Quantitative Analysis & Trading",
    terms=("quantitative analyst", "quantitative research", "quant developer",
           "quantitative strategy", "quantitative trader", "algorithmic trading",
           "quant analyst"),
    related=_near("data-science", "machine-learning") + _far("finance", "actuarial",
                                                             "software-engineering"),
    skills=("python", "c++", "numpy", "pandas", "kdb+", "stochastic calculus",
            "monte carlo", "time series", "derivatives", "backtesting", "r",
            "statistics", "linear algebra", "optimisation"),
))

# ---------------------------------------------------------------------------
# Product & Design
# ---------------------------------------------------------------------------

_add(Field(
    key="product-management",
    group="product-design",
    label="Product Management",
    terms=("product manager", "product owner", "product lead", "head of product",
           "technical product", "product director"),
    related=_near("design", "business-analysis")
            + _far("engineering-management", "business-intelligence", "marketing",
                   "project-management"),
    skills=("roadmap", "jira", "agile", "scrum", "user stories", "a/b testing",
            "okrs", "product discovery", "figma", "amplitude", "mixpanel",
            "stakeholder management", "prioritisation", "sql"),
))
_add(Field(
    key="design",
    group="product-design",
    label="UX & Product Design",
    terms=("ux designer", "ui designer", "product design", "interaction design",
           "visual design", "graphic design", "brand design", "design lead",
           "creative director", "motion design"),
    related=_near("product-management", "ux-research") + _far("frontend", "marketing"),
    skills=("figma", "sketch", "adobe xd", "photoshop", "illustrator", "invision",
            "prototyping", "wireframing", "design systems", "accessibility",
            "after effects", "indesign", "typography"),
))
_add(Field(
    key="ux-research",
    group="product-design",
    label="User Research",
    terms=("ux research", "user research", "design research", "usability",
           "research operations"),
    related=_near("design", "product-management") + _far("data-science"),
    skills=("usability testing", "interviews", "surveys", "personas",
            "journey mapping", "dovetail", "qualitative research", "ethnography"),
))
_add(Field(
    key="technical-writing",
    group="product-design",
    label="Technical Writing & Documentation",
    terms=("technical writer", "technical author", "documentation specialist",
           "content designer", "information developer", "ux writer"),
    related=_near("design") + _far("software-engineering", "content-communications"),
    skills=("markdown", "docs as code", "confluence", "madcap flare", "dita",
            "api documentation", "git", "style guide", "information architecture"),
))

# ---------------------------------------------------------------------------
# Science & Healthcare
# ---------------------------------------------------------------------------

_add(Field(
    key="pharma",
    group="science-health",
    label="Pharmaceutical & Medtech",
    terms=("pharmaceutical", "medical device", "medtech", "drug product",
           "biopharma", "qualified person", "validation engineer",
           "process development", "quality control analyst"),
    related=_near("laboratory", "manufacturing", "clinical-research")
            + _far("compliance", "operations"),
    skills=("gmp", "gxp", "hplc", "validation", "cleanroom", "capa", "deviation",
            "iso 13485", "fda", "ema", "lims", "batch record", "aseptic",
            "quality management system", "trackwise"),
))
_add(Field(
    key="biotech",
    group="science-health",
    label="Biotechnology & Bioprocessing",
    terms=("bioprocess", "biotechnology", "upstream process", "downstream process",
           "cell culture", "fermentation", "bioreactor", "molecular biology"),
    related=_near("pharma", "laboratory") + _far("manufacturing", "research-science"),
    skills=("cell culture", "chromatography", "pcr", "elisa", "bioreactor",
            "fermentation", "gmp", "assay development", "flow cytometry",
            "protein purification", "sterile technique"),
))
_add(Field(
    key="clinical-research",
    group="science-health",
    label="Clinical Research & Trials",
    terms=("clinical research", "clinical trial", "clinical data", "pharmacovigilance",
           "regulatory affairs", "medical affairs", "clinical operations",
           "drug safety"),
    related=_near("pharma", "healthcare") + _far("compliance", "laboratory"),
    skills=("gcp", "ich", "medra", "argus", "veeva", "edc", "clinical protocol",
            "case report form", "sas", "regulatory submission", "ctms"),
))
_add(Field(
    key="healthcare",
    group="science-health",
    label="Healthcare & Clinical Practice",
    terms=("nurse", "nursing", "physiotherapist", "radiographer", "pharmacist",
           "healthcare assistant", "clinical specialist", "occupational therapist",
           "medical doctor", "psychologist", "care assistant", "dietitian"),
    related=_near("clinical-research") + _far("public-sector", "education"),
    skills=("patient care", "nmbi", "coru", "clinical governance", "phlebotomy",
            "electronic health record", "safeguarding", "infection control",
            "triage", "medication administration"),
))
_add(Field(
    key="laboratory",
    group="science-health",
    label="Laboratory & Analytical Science",
    terms=("laboratory analyst", "lab technician", "analytical chemist",
           "microbiologist", "chemist", "lab scientist", "food scientist"),
    related=_near("pharma", "biotech") + _far("research-science", "manufacturing"),
    skills=("hplc", "gc-ms", "uv-vis", "titration", "spectroscopy", "lims",
            "iso 17025", "method validation", "microbiology", "sample preparation",
            "karl fischer", "wet chemistry"),
))

# ---------------------------------------------------------------------------
# Industrial & Built Environment
# ---------------------------------------------------------------------------

_add(Field(
    key="manufacturing",
    group="industry",
    label="Manufacturing & Production",
    terms=("manufacturing engineer", "process engineer", "production engineer",
           "production supervisor", "automation engineer", "maintenance engineer",
           "continuous improvement", "industrial engineer", "machine operator"),
    related=_near("mechanical-engineering", "supply-chain")
            + _far("operations", "pharma", "electrical-engineering"),
    skills=("lean", "six sigma", "kaizen", "plc", "scada", "cnc", "5s", "oee",
            "root cause analysis", "sap", "autocad", "solidworks", "tpm", "kanban"),
))
_add(Field(
    key="mechanical-engineering",
    group="industry",
    label="Mechanical & Design Engineering",
    terms=("mechanical engineer", "design engineer", "hvac engineer",
           "mechanical design", "cad engineer", "thermal engineer",
           "building services engineer"),
    related=_near("manufacturing", "civil-engineering")
            + _far("electrical-engineering", "energy", "embedded"),
    skills=("solidworks", "autocad", "catia", "inventor", "ansys", "fea", "cfd",
            "gd&t", "revit", "matlab", "tolerance analysis", "3d printing"),
))
_add(Field(
    key="electrical-engineering",
    group="industry",
    label="Electrical & Controls Engineering",
    terms=("electrical engineer", "controls engineer", "instrumentation",
           "electrical design", "power engineer", "electrician",
           "automation technician"),
    related=_near("manufacturing", "energy") + _far("mechanical-engineering", "embedded"),
    skills=("plc", "scada", "siemens", "allen bradley", "eplan", "autocad electrical",
            "hmi", "vfd", "instrumentation", "iec 61131", "single line diagram",
            "motor control", "dcs"),
))
_add(Field(
    key="civil-engineering",
    group="industry",
    label="Civil, Structural & Construction",
    terms=("civil engineer", "structural engineer", "quantity surveyor",
           "site engineer", "construction manager", "project engineer",
           "site manager", "architectural", "bim coordinator", "estimator"),
    related=_near("mechanical-engineering", "project-management")
            + _far("energy", "operations"),
    skills=("autocad", "revit", "bim", "tekla", "civil 3d", "primavera", "ms project",
            "structural analysis", "etabs", "cost planning", "nec contract",
            "health and safety", "setting out"),
))
_add(Field(
    key="energy",
    group="industry",
    label="Energy & Sustainability",
    terms=("renewable energy", "energy engineer", "sustainability", "wind energy",
           "solar", "esg analyst", "decarbonisation", "energy analyst",
           "environmental engineer"),
    related=_near("electrical-engineering", "civil-engineering")
            + _far("mechanical-engineering", "compliance", "operations"),
    skills=("energy modelling", "iso 50001", "ghg protocol", "life cycle assessment",
            "pvsyst", "scada", "grid connection", "carbon accounting", "esg reporting",
            "environmental impact assessment"),
))
_add(Field(
    key="supply-chain",
    group="industry",
    label="Supply Chain & Logistics",
    terms=("supply chain", "logistics", "procurement", "buyer", "warehouse operative",
           "warehouse manager", "warehouse supervisor", "demand planner",
           "production planner", "materials planner", "demand planning", "inventory",
           "freight", "materials manager", "category manager"),
    related=_near("operations", "manufacturing") + _far("finance", "retail"),
    skills=("sap", "oracle scm", "erp", "s&op", "incoterms", "wms", "kinaxis",
            "excel", "demand planning", "inventory management", "customs",
            "supplier management", "lean"),
))

# ---------------------------------------------------------------------------
# Finance & Legal
# ---------------------------------------------------------------------------

_add(Field(
    key="finance",
    group="finance-legal",
    label="Finance & Financial Services",
    terms=("financial analyst", "finance manager", "fp&a", "treasury", "controller",
           "investment analyst", "fund accountant", "credit analyst", "banking",
           "financial controller", "corporate finance", "portfolio analyst"),
    related=_near("accounting", "business-intelligence")
            + _far("audit-risk", "compliance", "operations", "quantitative-finance"),
    skills=("excel", "sap", "oracle financials", "netsuite", "hyperion", "vba",
            "financial modelling", "ifrs", "gaap", "power bi", "sql", "bloomberg",
            "forecasting", "variance analysis", "cash flow"),
))
_add(Field(
    key="accounting",
    group="finance-legal",
    label="Accounting & Tax",
    terms=("accountant", "accounts payable", "accounts receivable", "bookkeeper",
           "tax advisor", "tax manager", "payroll", "management accountant",
           "financial reporting", "part qualified"),
    related=_near("finance", "audit-risk") + _far("compliance", "operations"),
    skills=("acca", "aca", "cima", "cpa", "sage", "xero", "quickbooks", "excel",
            "ifrs", "gaap", "vat", "revenue online", "reconciliation",
            "month end close", "sap", "netsuite"),
))
_add(Field(
    key="audit-risk",
    group="finance-legal",
    label="Audit & Risk",
    terms=("auditor", "internal audit", "external audit", "risk analyst",
           "risk manager", "audit assurance", "operational risk", "credit risk",
           "financial crime", "anti money laundering"),
    related=_near("accounting", "compliance") + _far("finance", "security-engineering"),
    skills=("acca", "aca", "iia", "coso", "sox", "risk register", "aml", "kyc",
            "internal controls", "audit planning", "basel", "actimize", "excel"),
))
_add(Field(
    key="actuarial",
    group="finance-legal",
    label="Actuarial & Insurance",
    terms=("actuary", "actuarial", "underwriter", "insurance analyst", "reinsurance",
           "claims handler", "pricing analyst"),
    related=_near("finance", "quantitative-finance") + _far("audit-risk", "data-science"),
    skills=("r", "python", "excel", "vba", "sql", "prophet", "solvency ii", "ifrs 17",
            "reserving", "pricing", "glm", "sas", "moses"),
))
_add(Field(
    key="legal",
    group="finance-legal",
    label="Legal & Company Secretarial",
    terms=("solicitor", "legal counsel", "paralegal", "legal advisor", "barrister",
           "company secretary", "contracts manager", "legal executive",
           "data protection officer"),
    related=_near("compliance") + _far("audit-risk", "hr"),
    skills=("contract drafting", "gdpr", "due diligence", "litigation",
            "corporate governance", "commercial contracts", "legal research",
            "ip law", "employment law", "company law"),
))
_add(Field(
    key="compliance",
    group="finance-legal",
    label="Compliance & Regulatory",
    terms=("compliance officer", "compliance analyst", "regulatory compliance",
           "governance", "regulatory reporting",
           "conduct risk"),
    related=_near("audit-risk", "legal") + _far("finance", "security-engineering",
                                                "clinical-research"),
    skills=("gdpr", "aml", "kyc", "mifid", "central bank of ireland", "sox",
            "iso 27001", "risk assessment", "policy writing", "regulatory reporting",
            "training delivery"),
))

# ---------------------------------------------------------------------------
# Sales & Marketing
# ---------------------------------------------------------------------------

_add(Field(
    key="sales",
    group="commercial",
    label="Sales & Business Development",
    terms=("sales", "account executive", "account manager", "business development",
           "sales development", "sdr", "bdr", "inside sales", "field sales",
           "partnerships", "sales engineer", "pre-sales", "solutions consultant"),
    related=_near("customer-success", "marketing") + _far("operations", "retail",
                                                          "customer-support"),
    skills=("salesforce", "hubspot", "outreach", "meddic", "crm", "pipeline",
            "cold calling", "negotiation", "quota", "linkedin sales navigator",
            "gong", "forecasting", "saas"),
))
_add(Field(
    key="marketing",
    group="commercial",
    label="Marketing & Growth",
    terms=("marketing", "growth", "seo", "demand generation", "brand", "campaign",
           "product marketing", "performance marketing", "sem specialist",
           "social media", "crm manager", "paid media"),
    related=_near("sales", "content-communications")
            + _far("design", "product-management", "business-intelligence"),
    skills=("google analytics", "google ads", "hubspot", "marketo", "seo", "sem",
            "meta ads", "mailchimp", "salesforce", "a/b testing", "cro", "canva",
            "content strategy", "attribution", "klaviyo", "braze"),
))
_add(Field(
    key="content-communications",
    group="commercial",
    label="Content, Communications & PR",
    terms=("copywriter", "content", "communications", "public relations", "editor",
           "journalist", "social media manager", "press office"),
    related=_near("marketing") + _far("design", "technical-writing"),
    skills=("copywriting", "content strategy", "seo", "wordpress", "editing",
            "press release", "media relations", "storytelling", "canva",
            "social media", "newsletter"),
))
_add(Field(
    key="customer-success",
    group="commercial",
    label="Customer Success & Account Management",
    terms=("customer success", "client success", "account manager",
           "relationship manager", "onboarding specialist", "renewals",
           "client services"),
    related=_near("sales", "customer-support") + _far("operations", "project-management"),
    skills=("salesforce", "gainsight", "hubspot", "churn", "qbr", "onboarding",
            "upsell", "zendesk", "customer health score", "saas"),
))

# ---------------------------------------------------------------------------
# Business & Operations
# ---------------------------------------------------------------------------

_add(Field(
    key="operations",
    group="business-ops",
    label="Operations & General Management",
    terms=("operations", "business operations", "general manager",
           "operations manager", "service delivery", "process improvement",
           "chief of staff", "workplace", "facilities"),
    related=_near("project-management", "business-analysis")
            + _far("supply-chain", "finance", "sales", "manufacturing"),
    skills=("excel", "process mapping", "lean", "six sigma", "sop", "kpi",
            "vendor management", "sap", "asana", "budgeting", "reporting"),
))
_add(Field(
    key="project-management",
    group="business-ops",
    label="Project & Programme Management",
    terms=("project manager", "programme manager", "program manager", "scrum master",
           "delivery manager", "pmo", "agile coach", "project coordinator",
           "portfolio manager"),
    related=_near("operations", "business-analysis")
            + _far("engineering-management", "product-management", "civil-engineering"),
    skills=("jira", "confluence", "ms project", "primavera", "prince2", "pmp",
            "agile", "scrum", "safe", "kanban", "risk register", "gantt",
            "stakeholder management", "smartsheet"),
))
_add(Field(
    key="business-analysis",
    group="business-ops",
    label="Business Analysis",
    terms=("business analyst", "systems analyst", "process analyst",
           "requirements analyst", "functional consultant", "business partner"),
    related=_near("project-management", "business-intelligence", "product-management")
            + _far("operations", "consulting"),
    skills=("sql", "visio", "bpmn", "user stories", "jira", "requirements gathering",
            "process mapping", "uat", "excel", "power bi", "gap analysis"),
))
_add(Field(
    key="consulting",
    group="business-ops",
    label="Consulting & Strategy",
    terms=("consultant", "strategy manager", "strategy analyst", "head of strategy",
           "management consultant", "advisory", "transformation",
           "corporate development"),
    related=_near("business-analysis", "project-management")
            + _far("finance", "operations", "product-management"),
    skills=("powerpoint", "excel", "market research", "financial modelling",
            "business case", "stakeholder management", "benchmarking",
            "operating model", "change management"),
))
_add(Field(
    key="customer-support",
    group="business-ops",
    label="Customer Support",
    terms=("customer support", "customer service", "technical support",
           "support specialist", "trust and safety", "content moderator",
           "customer advisor", "contact centre"),
    related=_near("customer-success") + _far("sales", "operations", "it-support"),
    skills=("zendesk", "salesforce", "intercom", "jira service", "troubleshooting",
            "freshdesk", "sla", "csat", "ticketing", "live chat"),
))

# ---------------------------------------------------------------------------
# People & Talent
# ---------------------------------------------------------------------------

_add(Field(
    key="hr",
    group="people",
    label="Human Resources",
    terms=("human resources", "hr manager", "hr business partner", "hr generalist",
           "people operations", "people partner", "compensation and benefits",
           "employee relations", "learning and development", "hr advisor"),
    related=_near("recruiting") + _far("operations", "legal"),
    skills=("workday", "successfactors", "bamboohr", "employee relations",
            "performance management", "employment law", "payroll", "hris",
            "engagement survey", "onboarding", "cipd"),
))
_add(Field(
    key="recruiting",
    group="people",
    label="Recruitment & Talent Acquisition",
    terms=("recruiter", "recruitment", "talent acquisition", "talent partner",
           "sourcer", "headhunter", "resourcer", "talent scout"),
    related=_near("hr") + _far("sales", "operations"),
    skills=("greenhouse", "workday", "lever", "linkedin recruiter", "boolean search",
            "ats", "sourcing", "interviewing", "employer branding", "pipeline"),
))

# ---------------------------------------------------------------------------
# Education & Public Service
# ---------------------------------------------------------------------------

_add(Field(
    key="education",
    group="public-education",
    label="Education & Training",
    terms=("teacher", "lecturer", "tutor", "instructor", "education officer",
           "training specialist", "curriculum", "academic", "teaching assistant",
           "learning designer", "childcare"),
    related=_near("research-science") + _far("hr", "public-sector"),
    skills=("lesson planning", "curriculum design", "moodle", "canvas lms",
            "assessment", "safeguarding", "classroom management", "sen",
            "e-learning", "articulate"),
))
_add(Field(
    key="public-sector",
    group="public-education",
    label="Public Sector & Non-Profit",
    terms=("civil service", "public sector", "policy officer", "local authority",
           "charity", "non-profit", "community worker", "social worker",
           "grants officer", "executive officer"),
    related=_near("operations") + _far("compliance", "education", "healthcare"),
    skills=("policy analysis", "stakeholder engagement", "grant writing",
            "public procurement", "report writing", "freedom of information",
            "case management", "safeguarding"),
))

# ---------------------------------------------------------------------------
# Hospitality, Retail & Transport
# ---------------------------------------------------------------------------

_add(Field(
    key="hospitality",
    group="service",
    label="Hospitality, Food & Events",
    terms=("chef", "barista", "bartender", "waiter", "waitress", "hotel",
           "restaurant manager", "food and beverage", "event coordinator",
           "kitchen porter", "front office", "housekeeping", "concierge"),
    related=_near("retail") + _far("operations", "customer-support"),
    skills=("haccp", "food safety", "opera pms", "micros", "event management",
            "menu planning", "rostering", "customer service", "barista"),
))
_add(Field(
    key="retail",
    group="service",
    label="Retail & Consumer",
    terms=("retail", "store manager", "sales assistant", "merchandiser",
           "visual merchandising", "shop assistant", "shop manager", "e-commerce manager",
           "category buyer", "stock assistant"),
    related=_near("hospitality", "supply-chain") + _far("sales", "marketing",
                                                        "operations"),
    skills=("epos", "visual merchandising", "stock control", "shopify",
            "customer service", "cash handling", "planogram", "loss prevention"),
))
_add(Field(
    key="transport",
    group="service",
    label="Transport, Aviation & Driving",
    terms=("hgv driver", "van driver", "bus driver", "truck driver", "delivery driver",
           "aviation", "aircraft", "pilot", "cabin crew", "fleet manager",
           "fleet supervisor", "transport manager", "forklift", "maritime", "rail",
           "dispatcher"),
    related=_near("supply-chain") + _far("operations", "mechanical-engineering"),
    skills=("hgv", "cpc", "tachograph", "easa", "part 145", "route planning",
            "fleet management", "forklift licence", "safety management system",
            "load planning"),
))

# Every skill token known to the taxonomy, for resume extraction.
ALL_SKILLS: frozenset[str] = frozenset(
    skill for f in FIELDS.values() for skill in f.skills
)


def relatedness(keys: list[str] | tuple[str, ...]) -> dict[str, float]:
    """The neighbours one hop out from the chosen fields, and how close each one is.

    This is the "and related fields" half of the requirement. One hop is deliberate:
    two hops tends to reach the whole graph and stops meaning anything.

    Where two chosen fields disagree about a neighbour, the closer verdict wins: a
    searcher who ticked both Machine Learning and Backend has asked for enough of the
    software side that general Software Engineering is no longer a sideways move.
    Fields the searcher ticked outright are left out - they are not related to the
    search, they are the search.
    """
    chosen = {key for key in keys if key in FIELDS}
    neighbours: dict[str, float] = {}
    for key in chosen:
        for neighbour, closeness in FIELDS[key].related:
            if neighbour in FIELDS and neighbour not in chosen:
                neighbours[neighbour] = max(neighbours.get(neighbour, 0.0), closeness)
    return neighbours


def expand_fields(keys: list[str] | tuple[str, ...]) -> list[str]:
    """The chosen fields plus every neighbour, near or far, in a stable order."""
    expanded: list[str] = []
    for key in keys:
        field_obj = FIELDS.get(key)
        if not field_obj:
            continue
        if key not in expanded:
            expanded.append(key)
        for neighbour, _closeness in field_obj.related:
            if neighbour in FIELDS and neighbour not in expanded:
                expanded.append(neighbour)
    return expanded


def near_fields(keys: list[str] | tuple[str, ...]) -> list[str]:
    """The chosen fields plus only the neighbours close enough to search as if asked for.

    Used for the free-text half of the score. Feeding a far neighbour's vocabulary into
    the query pulls the whole ranking towards it a second time, on top of the field
    signal: an ML search that also searched every Software Engineering term scored
    "Software Developer Graduate" above the ML roles it was meant to surface.
    """
    near = relatedness(keys)
    chosen = [key for key in keys if key in FIELDS]
    return chosen + [key for key, closeness in near.items() if closeness >= NEAR]


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
