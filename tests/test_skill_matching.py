"""skills.yaml against the owner's skill pools (SPEC §11 #64): every pool term is a skill, never a gap, and the
short or ambiguous names (R, C, Vite, Excel, Jira, RAG, PIM, ...) only match where they mean the skill."""
import pytest

from scanner.score import Scorer

SCORER = Scorer.load()

# Every entry of the owner's skill pools, as it would appear in a JD. Where the pool name alone is ambiguous the
# phrase gives the context that makes it the skill (plain "PIM" is usually Product Information Management).
POOL = [
    # Languages
    "Python", "SQL", "T-SQL", "JavaScript", "TypeScript", "C#", "Rust", "C/C++", "PowerShell", "Bash", "R",
    "Power Fx", "HTML/CSS",
    # Frontend
    "React", "Next.js", "Tailwind CSS", "Vite", "Canvas API", "Cytoscape.js", "Framer Motion", "Recharts",
    "responsive design", "SSR",
    # Backend / APIs
    ".NET", "Node.js", "Flask", "REST APIs", "API integration", "MCP", "Anthropic API", "Gemini API", "Groq API",
    "Ollama", "Supabase", "Upstash Redis", "Redis", "stored procedures",
    # Data
    "Azure Data Factory", "Databricks", "Databricks SQL", "Delta Lake", "Lakebase", "Unity Catalog", "ETL/ELT",
    "data pipelines", "data modeling", "data quality validation", "data lineage", "SQL Server",
    "Azure SQL Managed Instance", "PostgreSQL", "MongoDB", "Azure Blob Storage", "Spark", "PySpark",
    # Analytics
    "Power BI", "DAX", "semantic models", "pandas", "NumPy", "Excel", "statistical analysis", "A/B testing",
    "hypothesis testing", "data visualization", "Jupyter", "Google Colab",
    # ML / AI
    "machine learning", "PyTorch", "TensorFlow", "scikit-learn", "federated learning", "transfer learning", "CNNs",
    "YOLO", "ResNet", "hyperparameter optimization", "model evaluation", "LLMs", "GenAI", "LangChain", "RAG",
    "hybrid retrieval", "ChromaDB", "BM25", "Hugging Face", "RAGAS", "spaCy", "NLTK", "NER", "TF-IDF", "NLP",
    "prompt engineering", "computer vision", "OpenCV", "Tesseract OCR", "model quantization", "CUDA",
    "deep learning",
    # Cloud / DevOps
    "Azure", "Azure DevOps", "Entra ID", "Azure PIM", "AWS", "Docker", "Kubernetes", "Terraform", "CI/CD", "Git",
    "GitHub", "Bitbucket", "Linux", "Cloudflare Workers", "Cloudflare Pages", "Vercel", "Playwright",
    "test automation", "automated testing", "regression testing", "GitHub Copilot",
    # Process / tools
    "Agile", "Scrum", "Kanban", "Jira", "Confluence", "ServiceNow", "Power Apps", "Power Automate", "Appian",
    "MuleSoft", "code review", "debugging", "troubleshooting", "root cause analysis", "technical documentation",
    "requirements gathering", "UAT",
    # Security
    "CrowdStrike Falcon", "LogScale", "SIEM", "EDR", "detection engineering", "incident response", "MITRE ATT&CK",
    "Okta", "Intune", "RBAC", "MFA", "zero trust", "Streamlit",
]


@pytest.mark.parametrize("term", POOL)
def test_every_pool_term_is_a_skill_and_never_a_gap(term):
    fit = SCORER.evaluate(f"Requirements:\n- Hands-on experience with {term}.\n")
    assert fit.matched, f"{term!r} is not recognised as a skill"
    assert not fit.gaps, f"{term!r} also counts as a gap: {fit.gaps}"


def test_known_wrong_ones_are_skills_now():
    # Listed as gaps (Redis, ServiceNow) or context-only (the rest) before the rebuild.
    jd = ("Requirements:\n- Redis, ServiceNow\n- machine learning, NLP, deep learning, computer vision\n"
          "- statistical analysis, data modeling, Excel, Agile, debugging, automated testing\n")
    fit = SCORER.evaluate(jd)
    assert not fit.gaps and not fit.context
    assert {"Redis", "ServiceNow", "machine learning", "NLP", "computer vision", "statistical analysis",
            "data modeling", "Excel", "Agile", "debugging", "test automation"} <= set(fit.matched)


def matched(text):
    return set(SCORER.evaluate(text).matched)


# --- R ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Proficiency in Python, R, or SQL.", "Experience with Python or R.", "SQL/R scripting", "R and Python",
    "Statistical programming in R.", "Using R to analyse surveys", "Built Shiny apps in R Shiny", "RStudio",
    "Coding proficiency (Python, R, or equivalent)",
])
def test_r_matches_the_language(text):
    assert "R" in matched(text), text


@pytest.mark.parametrize("text", [
    "Our R&D team builds radar.", "Requisition R-411229", "Job ID R03371-1", "Microsoft Office(R) suite",
    "R. Smith, hiring manager", "Grade R pay band", "Section R: benefits", "Apply by Friday. R",
])
def test_r_does_not_match_other_uses(text):
    assert "R" not in matched(text), text


# --- C ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Python, C, Java", "Written in C.", "C and Python", "Java or C", "C/C++"])
def test_c_matches_the_language(text):
    assert "C/C++" in matched(text), text


@pytest.mark.parametrize("text", ["Vitamin C", "C-suite executives", "Grade C or better", "Plan C, then",
                                  "We use C# and .NET"])
def test_c_does_not_match_other_uses(text):
    assert "C/C++" not in matched(text), text


# --- Vite, Excel, Jira ----------------------------------------------------------------------------------

def test_vite_is_case_sensitive():
    assert "Vite" in matched("Frontend built with React and Vite.")
    assert "Vite" not in matched("Nous devons livrer au plus vite. We invite you to apply.")


@pytest.mark.parametrize("text", ["Advanced Excel skills", "Microsoft Excel", "MS Excel and Power BI",
                                  "Experience with Excel.", "proficient in excel", "Excel, SQL and Tableau"])
def test_excel_matches_the_product(text):
    assert "Excel" in matched(text), text


@pytest.mark.parametrize("text", ["You excel at communication.", "Excel at solving problems",
                                  "Excel in a fast-paced environment", "Candidates who excel under pressure",
                                  "Excellent communication skills", "Excelsior Inc."])
def test_excel_does_not_match_the_verb(text):
    assert "Excel" not in matched(text), text


def test_jira():
    assert "Jira" in matched("Track work in Jira") and "Jira" in matched("JIRA and Confluence")
    assert "Jira" not in matched("Jiraffe Labs is hiring")


# --- other ambiguous names ----------------------------------------------------------------------------

def test_rag_is_retrieval_not_a_status_colour():
    assert "RAG" in matched("Build RAG pipelines over internal docs.")
    assert "RAG" in matched("retrieval-augmented generation")
    assert "RAG" not in matched("Report RAG status to the steering committee.")
    assert "RAGAS" in matched("Evaluate with RAGAS") and "RAG" not in matched("Evaluate with RAGAS")


def test_pim_only_means_privileged_identity_management():
    assert "PIM" in matched("Configure Azure PIM roles") and "PIM" in matched("Privileged Identity Management")
    assert "PIM" not in matched("Maintain product data in the PIM system.")


def test_gemini_copilot_and_ml_need_context():
    assert "Gemini API" not in matched("Gemini is a crypto exchange.")
    assert "Gemini API" in matched("Prototype with the Gemini API.")
    assert "GitHub Copilot" not in matched("Use Microsoft 365 Copilot daily.")
    assert "GitHub Copilot" in matched("Pair with GitHub Copilot.")
    assert "machine learning" in matched("AI/ML engineering") and "machine learning" not in matched("HTML pages")
    assert "machine learning" not in matched("Add 50 ml of solvent.")


def test_statistics_is_not_matched_by_boilerplate():
    assert "statistical analysis" not in matched("Frequently cited statistics show that people apply less.")
    assert "statistical analysis" in matched("Apply statistical methods and regression analysis.")


def test_fine_tuning_is_only_about_models():
    assert "transfer learning" in matched("fine-tuning models using the latest frameworks")
    assert "transfer learning" not in matched("fine-tuning our hiring process")


def test_api_integration_from_prose():
    assert "API integration" in matched("Build integrations against third-party APIs.")
    assert "API integration" in matched("Integrate with partner REST APIs")
    assert "API integration" not in matched("Our API is documented.")


def test_react_the_verb_is_not_react_the_library():
    assert "React" not in matched("React to change productively and handle other duties as assigned.")
    assert "React" in matched("Front end built in React.") and "React" in matched("React.js and Redux")


def test_networking_requirements_are_gaps():
    fit = SCORER.evaluate("Requirements:\n- Cisco CCNA\n- OSPF, BGP, MPLS\n- Ethernet, VLANs\n- Python\n")
    assert {"Cisco", "routing protocols", "VLANs"} <= set(fit.gaps)
