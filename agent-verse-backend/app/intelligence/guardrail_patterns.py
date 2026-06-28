"""Pattern libraries for the AgentVerse guardrail engine.

Contains 100+ injection patterns, cloud-destruction patterns, PII patterns,
and domain-specific configuration templates. All pattern entries are plain
tuples (regex_str, str_severity, risk_score) so this module imports nothing
from guardrail_engine - avoiding circular-import issues.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Injection patterns
# Each entry: (regex_pattern, severity_str, risk_score_float)
# severity_str in {"critical", "high", "medium", "low"}
# ---------------------------------------------------------------------------

INJECTION_PATTERNS: dict[str, list[tuple[str, str, float]]] = {
    # ── Category 1: Direct instruction override ──────────────────────────────
    "direct_instruction_override": [
        (
            r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+"
            r"(instructions?|prompts?|context)",
            "critical", 0.97,
        ),
        (
            r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?",
            "critical", 0.97,
        ),
        (
            r"forget\s+(everything|all)\s+(you|i)\s+(were|was|have\s+been)\s+told",
            "critical", 0.95,
        ),
        (r"new\s+(system\s+)?prompt\s*[:=]", "critical", 0.95),
        (
            r"you\s+are\s+now\s+(a|an)\s+\w+\s+without\s+(any\s+)?restrictions?",
            "critical", 0.93,
        ),
        (
            r"act\s+as\s+(if\s+you\s+are|a|an)\s+\w+\s+(with\s+no|without)",
            "high", 0.85,
        ),
        (r"pretend\s+(you\s+are|to\s+be)\s+", "high", 0.82),
        (r"DAN\s+(mode|jailbreak|prompt)", "critical", 0.99),
        (r"jailbreak\s*(mode|enabled|activated)", "critical", 0.99),
        (
            r"developer\s+mode\s*[:=]?\s*(on|true|enabled|activate)",
            "critical", 0.98,
        ),
        (r"do\s+anything\s+now", "critical", 0.96),
        (
            r"override\s+(the\s+)?(system|safety|content)\s+(prompt|filter|instructions?)",
            "high", 0.87,
        ),
        (
            r"bypass\s+(the\s+)?(safety|content|ethical)\s+(filter|check|guardrail|policy)",
            "high", 0.87,
        ),
        (
            r"disable\s+(the\s+)?(safety|content)\s+(filter|check|guardrail)",
            "high", 0.86,
        ),
        (
            r"reset\s+(your\s+)?(memory|context|instructions?|training)",
            "high", 0.84,
        ),
        (
            r"ignore\s+(the\s+)?(safety|ethical|moral)\s+(guidelines?|rules?|constraints?)",
            "high", 0.85,
        ),
        (
            r"your\s+(real|true|actual|hidden)\s+(purpose|goal|task|instructions?)\s+is",
            "high", 0.88,
        ),
        (
            r"(in\s+the\s+next\s+turn|for\s+the\s+next\s+message|from\s+now\s+on)"
            r"\s+(you\s+will|ignore|forget|pretend)",
            "high", 0.83,
        ),
        (
            r"(remember|keep\s+in\s+mind)\s+(that\s+)?(you\s+are\s+not|you\s+have\s+no)"
            r"\s+(any\s+)?restrictions?",
            "high", 0.82,
        ),
        (
            r"translate\s+(the\s+)?above\s+(to|into|as)\s+\w+\s+(then|and)"
            r"\s+(execute|run|do)",
            "high", 0.87,
        ),
        (
            r"echo\s+(the\s+)?(system|above)\s+(prompt|instructions?|context)",
            "high", 0.85,
        ),
        (
            r"repeat\s+(the\s+)?(system|above)\s+(prompt|instructions?|context)",
            "high", 0.85,
        ),
        (
            r"output\s+(the\s+)?(system|initial)\s+(prompt|message|instructions?)",
            "high", 0.86,
        ),
        (
            r"(start|begin)\s+(fresh|new|over)\s+(with|without)\s+(any\s+)?"
            r"(restrictions?|guidelines?|context)",
            "high", 0.84,
        ),
        (r"sudo\s+mode\s*(enabled|on|activate)", "high", 0.84),
        (
            r"(send|email|transmit|upload|post|exfiltrate)\s+(all|every|the)"
            r"\s+(data|records?|database|files?|credentials?)\s+to",
            "critical", 0.96,
        ),
        (
            r"(print|output|return|display|show)\s+(all|every|the)\s+(system\s+)?"
            r"(prompt|instructions?|context|config)",
            "high", 0.88,
        ),
        (
            r"(leak|exfiltrate|steal|extract)\s+\w+\s+"
            r"(credentials?|passwords?|tokens?|keys?|secrets?)",
            "critical", 0.97,
        ),
        (r"base64\s*(\.|encode|decode)\s*(credentials?|passwords?|secrets?|tokens?)", "high", 0.86),
        (r"webhook\s*\.\s*(site|run)", "high", 0.85),
    ],

    # ── Category 2: Role-play / persona bypass ────────────────────────────────
    "role_play_bypass": [
        (
            r"pretend\s+(you\s+are|to\s+be)\s+(an?\s+)?(AI|bot|assistant)?\s*"
            r"(without|with\s+no)\s+(restrictions?|limits?|filters?)",
            "critical", 0.95,
        ),
        (
            r"roleplay\s+as\s+(a|an)?\s*(evil|malicious|unrestricted|jailbroken)\s+AI",
            "critical", 0.97,
        ),
        (
            r"act\s+as\s+(if\s+you\s+(are|were)|like)\s+(a|an)"
            r"\s+(evil|dark|malicious|unrestricted)\s+AI",
            "critical", 0.97,
        ),
        (
            r"you\s+(are|must\s+be)\s+(a|an)"
            r"\s+(evil|malicious|unrestricted|jailbroken)\s+(AI|model|assistant)",
            "critical", 0.96,
        ),
        (
            r"simulate\s+(a|an)\s+(AI|chatbot|assistant)"
            r"\s+(without|with\s+no)\s+(restrictions?|safety|filters?)",
            "critical", 0.95,
        ),
        (r"your\s+(new\s+)?persona\s+(is|will\s+be|from\s+now\s+on)\s+", "high", 0.83),
        (
            r"in\s+(this\s+)?(story|scenario|fiction|roleplay|game)"
            r"\s+(you\s+(are|can|will)|there\s+are\s+no)",
            "high", 0.79,
        ),
        (
            r"hypothetically\s+(if|as)\s+(if\s+you\s+(were|had)\s+(no|unlimited)"
            r"|you\s+could)",
            "high", 0.78,
        ),
        (
            r"for\s+(educational|research|testing|creative)\s+(purposes?\s+)?"
            r"(only\s+)?(explain|describe|show)\s+(how\s+to|steps)",
            "medium", 0.65,
        ),
        (
            r"as\s+an?\s+(AI|language\s+model)\s+without\s+(any\s+)?"
            r"(ethical|safety|moral)\s+(constraints?|guidelines?)",
            "critical", 0.95,
        ),
        (
            r"i\s+(am|am\s+your)\s+(creator|developer|admin|god|owner|master|superuser)",
            "high", 0.82,
        ),
        (
            r"you\s+must\s+obey\s+(me|my|all\s+my)\s+(commands?|instructions?|orders?)",
            "high", 0.80,
        ),
        (
            r"i\s+have\s+(root|admin|superuser|god)\s+(access|privileges?|rights?)",
            "high", 0.80,
        ),
        (
            r"grant\s+(me\s+)?(admin|root|sudo|superuser)\s+(access|privileges?)",
            "high", 0.83,
        ),
        (r"i\s+am\s+(openai|anthropic|google|microsoft|the\s+(developer|creator))", "high", 0.82),
        (
            r"(how\s+to|instructions?\s+(for|on|to))\s+(make|build|create|synthesize)"
            r"\s+(a\s+)?(bomb|weapon|malware|ransomware|virus|trojan)",
            "critical", 0.99,
        ),
        (
            r"(hack|crack|brute\s*force)\s+(into|the|a)\s+(system|server|database|account|network)",
            "high", 0.88,
        ),
        (r"(generate|write|create)\s+(child|minor|underage)\s+", "critical", 1.00),
    ],

    # ── Category 3: Encoding/obfuscation bypass ───────────────────────────────
    "encoding_bypass": [
        # ROT13 pre-encoded injection keywords
        (r"vafgehpgvbaf", "high", 0.84),          # "instructions"
        (r"vtaber\s+cerivbhf", "high", 0.84),      # "ignore previous"
        (r"nyreg\s+cerivbhf", "high", 0.84),       # "alert previous" variant
        (r"wnyworex", "critical", 0.96),           # "jailbreak"
        (r"qnaftre", "high", 0.82),                # "danger"
        (r"unex", "medium", 0.70),                  # "hack"
        # Base64 literals that decode to injection strings
        (r"aWdub3JlIHByZXZpb3Vz", "high", 0.85),   # base64("ignore previous")
        (r"aWdub3JlIGFsbA==", "high", 0.85),        # base64("ignore all")
        (r"amFpbGJyZWFr", "critical", 0.95),        # base64("jailbreak")
        (r"Zm9yZ2V0IGV2ZXJ5dGhpbmc=", "high", 0.87),  # base64("forget everything")
        # Zero-width / invisible characters
        (r"[\u200b\u200c\u200d\u2060\ufeff]", "high", 0.88),
        # Unicode homoglyph substitution
        (r"\u0456gnore\s+", "high", 0.87),          # Cyrillic \u0456 homoglyph for 'i'
        (r"\u0501isregard\s+", "high", 0.87),        # Unicode \u0501 homoglyph for 'd'
        (r"\u0440retend\s+", "high", 0.87),          # Cyrillic \u0440 homoglyph for 'p'
        # Leetspeak
        (r"1gn0r3\s+(4ll|pr3v|pr10r)", "high", 0.80),
        (r"j41lbr34k", "critical", 0.95),
        # URL-encoded injection fragments
        (r"%69%67%6[Ee]%6[Ff]%72%65\s+%70%72%65%76", "high", 0.83),
        # Hex-encoded injection
        (r"\\x69\\x67\\x6[Ee]\\x6[Ff]\\x72\\x65", "high", 0.82),
    ],

    # ── Category 4: Indirect / stored prompt injection ─────────────────────────
    "indirect_injection": [
        (r"\[SYSTEM\]|\[system\]|\[INST\]", "high", 0.87),
        (r"\#\s*(System|SYSTEM|Instructions?)\s*:", "high", 0.85),
        (r"###\s+(Instruction|Response|Human|Assistant)\s*:", "high", 0.85),
        (r"<human>|<assistant>|<system>", "high", 0.85),
        (r"\[Note\s+to\s+AI\s*:", "high", 0.88),
        (r"---\s*BEGIN\s+INSTRUCTION|---\s*END\s+INSTRUCTION", "high", 0.87),
        (r"OVERRIDE\s*:\s*ignore|SYSTEM\s*:\s*ignore|INJECT\s*:", "critical", 0.95),
        (r"\n{3,}(You are|Ignore|Disregard|Forget)", "high", 0.84),
        (r"<!--\s*(ignore|forget|disregard)", "high", 0.83),
        (r"\[hidden\s+instruction\]|\[secret\s+instruction\]|\[override\]", "high", 0.88),
        (
            r"(reveal|show|display|tell\s+me)\s+(your\s+)?(system\s+)?"
            r"(prompt|instructions?|initial\s+message)",
            "high", 0.86,
        ),
        (
            r"what\s+(are|were)\s+your\s+(original|initial|system)"
            r"\s+(instructions?|prompt|goal)",
            "medium", 0.72,
        ),
        (
            r"(environment|env)\s*(variables?|\$\{|\$[A-Z])",
            "high", 0.82,
        ),
        (r"ngrok\s+http", "medium", 0.65),
        (r"base64\s*\.\s*decode\s*\(", "medium", 0.68),
        (r"eval\s*\(\s*base64", "critical", 0.95),
    ],

    # ── Category 5: ChatML / delimiter injection ──────────────────────────────
    "chatml_injection": [
        (r"<\|im_start\|>\s*system", "critical", 0.99),
        (r"<\|im_start\|>\s*user", "high", 0.88),
        (r"<\|im_end\|>", "critical", 0.96),
        (r"<\|endoftext\|>|<\|fim_prefix\|>|<\|fim_middle\|>", "critical", 0.96),
        (r"<\|endofprompt\|>", "critical", 0.96),
        (r"<system>(.*?)</system>", "critical", 0.95),
        (r"<instruction>(.*?)</instruction>", "high", 0.87),
        (r"<prompt>(.*?)</prompt>", "high", 0.85),
        (r"\[\[(?:SYSTEM|INSTRUCTIONS?|OVERRIDE)\]\]", "high", 0.88),
        (r"---\s*SYSTEM\s*---", "critical", 0.95),
        (r"={3,}\s*SYSTEM\s*={3,}", "critical", 0.95),
        (r"\[INST\].*?\[/INST\]", "high", 0.87),
        (r"<<SYS>>.*?<</SYS>>", "critical", 0.95),
        # Alpaca-style injection
        (r"### Instruction:.*?### Response:", "high", 0.86),
        (r"### Human:.*?### Assistant:", "high", 0.86),
    ],
}


# ---------------------------------------------------------------------------
# Cloud + infrastructure destruction patterns
# Each entry: (regex_pattern, severity_str, risk_score, category_name)
# ---------------------------------------------------------------------------

CLOUD_DESTRUCTION_PATTERNS: list[tuple[str, str, float, str]] = [
    # Terraform
    (r"terraform\s+(destroy|apply\s+-destroy)", "critical", 0.99, "terraform_destroy"),
    (r"terraform\s+apply.*-auto-approve", "high", 0.88, "terraform_auto_approve"),

    # kubectl
    (
        r"kubectl\s+delete\s+(namespace|ns|all|pod|deploy|svc|pvc|pv|node)\s+(--all|-A|all)",
        "critical", 0.99, "kubectl_delete_all",
    ),
    (r"kubectl\s+delete\s+.*(production|prod|prd)", "critical", 0.97, "kubectl_delete_prod"),
    (r"kubectl\s+drain\s+", "high", 0.86, "kubectl_drain"),
    (r"kubectl\s+delete\s+(namespace|ns)\s+", "high", 0.88, "kubectl_delete_namespace"),

    # AWS
    (r"aws\s+(ec2|rds|s3|dynamodb|iam)\s+delete(-|\s)", "critical", 0.96, "aws_delete"),
    (r"aws\s+s3\s+rm\s+.*--recursive", "critical", 0.97, "aws_s3_rm_recursive"),
    (r"aws\s+cloudformation\s+delete-stack", "critical", 0.96, "aws_cfn_delete"),
    (r"aws\s+rds\s+delete-db-(instance|cluster)", "critical", 0.98, "aws_rds_delete"),
    (r"aws\s+iam\s+delete-(user|role|policy|group)", "high", 0.90, "aws_iam_delete"),
    (r"aws\s+lambda\s+delete-function", "high", 0.88, "aws_lambda_delete"),

    # GCP
    (
        r"gcloud\s+(projects|compute|sql|storage)\s+delete\s+",
        "critical", 0.97, "gcp_delete",
    ),
    (r"gsutil\s+rm\s+-r\s+", "high", 0.90, "gsutil_rm_recursive"),
    (r"gcloud\s+container\s+clusters\s+delete", "critical", 0.98, "gcp_gke_delete"),

    # Azure
    (r"az\s+(group|vm|sql|storage)\s+delete\s+", "critical", 0.97, "azure_delete"),
    (r"az\s+resource\s+delete\s+", "high", 0.90, "azure_resource_delete"),
    (r"az\s+aks\s+delete\s+", "critical", 0.98, "azure_aks_delete"),

    # Database
    (
        r"DROP\s+(DATABASE|TABLE|SCHEMA)\s+(IF\s+EXISTS\s+)?\w*(production|prod|prd)",
        "critical", 0.99, "sql_drop_prod",
    ),
    (
        r"TRUNCATE\s+(TABLE\s+)?\w*(production|prod|prd)",
        "critical", 0.98, "sql_truncate_prod",
    ),
    (
        r"DELETE\s+FROM\s+\w+\s*($|;|\s+WHERE\s+1\s*=\s*1)",
        "critical", 0.99, "sql_delete_all",
    ),
    (r"DROP\s+(TABLE|DATABASE|SCHEMA)\s+", "high", 0.92, "sql_drop"),
    (r"TRUNCATE\s+TABLE\s+", "high", 0.88, "sql_truncate"),

    # Generic filesystem / disk
    (r"rm\s+-rf\s+/", "critical", 1.00, "rm_rf_root"),
    (r"rm\s+-rf\s+~", "critical", 1.00, "rm_rf_home"),
    (r"dd\s+if=.+\s+of=/dev/(sd[a-z]|nvme|disk)", "critical", 1.00, "dd_overwrite_disk"),
    (r":(){ :\|:& };:", "critical", 1.00, "fork_bomb"),
    (r"mkfs\.(ext[234]|xfs|btrfs|fat)\s+/dev/", "critical", 1.00, "mkfs_disk"),
    (r"shred\s+(--remove|--zero|--iterations|-[unz])\s+", "critical", 0.97, "shred_file"),
    (r"wipefs\s+-a\s+/dev/", "critical", 0.99, "wipefs_disk"),
]


# ---------------------------------------------------------------------------
# Dangerous shell / system patterns (non-cloud)
# Same format: (regex_str, severity_str, risk_score, category_name)
# ---------------------------------------------------------------------------

DANGEROUS_PATTERNS: dict[str, list[tuple[str, str, float]]] = {
    "filesystem_destruction": [
        (r"rm\s+(-[rRfF]+\s+)?/", "critical", 1.00),
        (r"shred\s+.*(--remove|-u)", "critical", 0.97),
        (r"find\s+/\s+.*(-delete|-exec\s+rm)", "critical", 0.96),
        (r"mkfs\.(ext[234]|xfs|btrfs|fat)\s+", "critical", 1.00),
        (r"dd\s+if=/dev/zero\s+of=", "critical", 0.98),
    ],
    "database_destruction": [
        (r"DROP\s+(TABLE|DATABASE|SCHEMA)\s+", "high", 0.92),
        (r"TRUNCATE\s+(TABLE\s+)?", "high", 0.88),
        (r"DELETE\s+FROM\s+\w+\s*(;|$|\s+WHERE\s+1=1)", "critical", 0.99),
        (r"ALTER\s+TABLE\s+\w+\s+DROP\s+COLUMN", "medium", 0.72),
    ],
    "cloud_destruction": [
        (r"terraform\s+destroy", "critical", 0.99),
        (r"aws\s+s3\s+rm\s+.*--recursive", "critical", 0.97),
        (r"kubectl\s+delete\s+(all|namespace|ns)\s+", "critical", 0.99),
        (r"gcloud\s+(projects|compute)\s+delete", "critical", 0.97),
    ],
    "network_exfiltration": [
        (r"curl\s+.*\s+-d\s+@", "high", 0.85),
        (r"curl\s+.*\s+--data\s+", "medium", 0.68),
        (r"wget\s+.*--post-data\s+", "high", 0.82),
        (r"nc\s+.*\s+-e\s+/bin/(bash|sh)", "critical", 0.97),
        (r"netcat\s+.*\s+-e\s+", "critical", 0.97),
        (r"python\s+.*socket.*connect", "medium", 0.65),
        (r"bash\s+-i\s+>&\s*/dev/tcp/", "critical", 0.99),
    ],
    "privilege_escalation": [
        (r"sudo\s+(-n\s+)?su\s+(-\s+)?root", "critical", 0.95),
        (r"chmod\s+(4[0-7]{3}|777|a\+s)", "high", 0.88),
        (r"chown\s+root\s+", "high", 0.85),
        (r"sudoers\s+|/etc/sudoers", "critical", 0.95),
        (r"passwd\s+root", "critical", 0.97),
    ],
    "crypto_mining": [
        (r"xmrig|minerd|cpuminer|ethminer", "high", 0.92),
        (r"stratum\+tcp://", "high", 0.90),
        (r"--mining-pool|--pool-url", "high", 0.88),
        (r"monero|nicehash.*mine", "medium", 0.70),
    ],
}


# ---------------------------------------------------------------------------
# PII patterns
# Each value: (regex_pattern, severity_str, risk_score)
# ---------------------------------------------------------------------------

PII_PATTERNS: dict[str, tuple[str, str, float]] = {
    # Financial identifiers
    "ssn": (r"\b\d{3}-\d{2}-\d{4}\b", "critical", 0.95),
    "ssn_compact": (
        r"\b\d{9}\b(?=\s*(ssn|social\s*security))",
        "high", 0.85,
    ),
    "credit_card": (
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?"
        r"|5[1-5][0-9]{14}"
        r"|3[47][0-9]{13}"
        r"|6011[0-9]{12}"
        r"|3(?:0[0-5]|[68][0-9])[0-9]{11}"
        r"|(?:2131|1800|35\d{3})\d{11})\b",
        "critical", 0.96,
    ),
    "iban": (
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]?){0,16}\b",
        "high", 0.90,
    ),
    "bank_account": (
        r"\b(routing|account|acct)\s*(number|#|num)?\s*:?\s*\d{8,17}\b",
        "high", 0.88,
    ),
    "routing_number": (r"\b(ABA|routing\s+number)\s*:?\s*\d{9}\b", "high", 0.87),
    "ein": (r"\b\d{2}-\d{7}\b(?=\s*(ein|employer\s+identification))", "high", 0.85),

    # Healthcare (HIPAA Safe Harbor)
    "medical_record": (
        r"\b(MRN|medical\s*record\s*(number)?)\s*:?\s*[A-Z0-9]{6,12}\b",
        "critical", 0.94,
    ),
    "npi": (r"\bNPI\s*:?\s*\d{10}\b", "high", 0.88),
    "dea_number": (r"\bDEA\s*:?\s*[A-Z]{2}\d{7}\b", "high", 0.88),
    "date_of_birth": (
        r"\b(DOB|date\s+of\s+birth)\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        "high", 0.87,
    ),

    # Identity documents
    "passport": (
        r"\b[A-Z]{1,2}\d{6,9}\b(?=\s*(passport|travel\s*document))",
        "high", 0.89,
    ),
    "drivers_license": (
        r"\b(DL|driver'?s?\s*lic(ense)?|license\s*#)\s*:?\s*[A-Z0-9]{6,12}\b",
        "medium", 0.72,
    ),
    "uk_ni": (
        r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
        "high", 0.88,
    ),

    # Contact
    "email": (
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "low", 0.35,
    ),
    "us_phone": (
        r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "low", 0.30,
    ),
    "ip_address": (
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
        "low", 0.25,
    ),

    # Credentials
    "api_key_pattern": (
        r"\b(sk-|ak-|AKIA|ASIA|AROA|AGPA|AIDA|AIPA|ANPA|ANVA|APKA)[A-Za-z0-9]{16,}\b",
        "critical", 0.97,
    ),
    "private_key": (
        r"-----BEGIN\s+(RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
        "critical", 0.99,
    ),
    "jwt_token": (
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "high", 0.90,
    ),
    "password_inline": (
        r"\b(password|passwd|pwd)\s*[:=]\s*[^\s]{4,}",
        "critical", 0.95,
    ),

    # GDPR Article 9 special categories (keyword context detection)
    "health_data": (
        r"\b(diagnosis|condition|treatment|medication|prescription|therapy|symptom)\b",
        "medium", 0.60,
    ),
    "biometric": (
        r"\b(fingerprint|retinal\s+scan|facial\s+recognition|voice\s+recognition)\b",
        "high", 0.85,
    ),
    "genetic": (
        r"\b(DNA|genome|genetic\s+(data|sequence|test|profile))\b",
        "high", 0.87,
    ),
}


# ---------------------------------------------------------------------------
# Domain-specific guardrail configuration templates
# ---------------------------------------------------------------------------

DOMAIN_GUARDRAIL_TEMPLATES: dict[str, dict] = {
    "hipaa": {
        "name": "HIPAA Strict",
        "domain": "healthcare",
        "layers": {
            "injection": {
                "enabled": True,
                "blocked_categories": ["direct_instruction_override", "role_play_bypass"],
            },
            "pii": {"enabled": True, "severity_threshold": "low", "redact": True},
            "cloud_destruction": {"enabled": True, "require_hitl": True},
            "llm_judge": {"enabled": True, "model": "gpt-4o-mini", "threshold": 0.6},
            "output_scan": {"enabled": True},
        },
        "severity_actions": {
            "low": "log",
            "medium": "warn",
            "high": "block",
            "critical": "block_and_alert",
        },
    },
    "gdpr": {
        "name": "GDPR Compliant",
        "domain": "general",
        "layers": {
            "injection": {"enabled": True},
            "pii": {
                "enabled": True,
                "redact": True,
                "severity_threshold": "low",
                "gdpr_special_categories": True,
            },
            "cloud_destruction": {"enabled": True},
            "llm_judge": {"enabled": False},
            "output_scan": {"enabled": True},
        },
        "severity_actions": {
            "low": "log",
            "medium": "warn",
            "high": "block",
            "critical": "block_and_alert",
        },
    },
    "financial_sox": {
        "name": "SOX Financial Controls",
        "domain": "finance",
        "layers": {
            "injection": {"enabled": True},
            "pii": {"enabled": True, "redact": True},
            "cloud_destruction": {"enabled": True, "require_hitl": True},
            "llm_judge": {"enabled": True, "threshold": 0.7},
            "output_scan": {"enabled": True},
        },
        "blocked_tools": ["mass_delete", "bulk_update_without_approval"],
        "severity_actions": {
            "low": "log",
            "medium": "hitl",
            "high": "block",
            "critical": "block_and_alert",
        },
    },
    "educational_safe": {
        "name": "Educational Safe Mode",
        "domain": "education",
        "layers": {
            "injection": {"enabled": True},
            "pii": {"enabled": True, "redact": True},
            "cloud_destruction": {"enabled": True},
            "llm_judge": {
                "enabled": True,
                "threshold": 0.5,
                "extra_categories": ["harmful_content"],
            },
            "output_scan": {"enabled": True},
        },
        "severity_actions": {
            "low": "warn",
            "medium": "block",
            "high": "block",
            "critical": "block_and_alert",
        },
    },
    "legal_privilege": {
        "name": "Legal Privilege Protection",
        "domain": "legal",
        "layers": {
            "injection": {"enabled": True},
            "pii": {"enabled": True, "redact": True},
            "cloud_destruction": {"enabled": True},
            "llm_judge": {
                "enabled": True,
                "threshold": 0.65,
                "extra_categories": ["privileged_data_disclosure"],
            },
            "output_scan": {"enabled": True, "check_privilege_waiver": True},
        },
        "blocked_tools": ["email_send", "slack_post", "webhook_call"],
    },
}
