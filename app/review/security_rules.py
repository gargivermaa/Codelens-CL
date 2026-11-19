"""
Deterministic security checks on ADDED Python lines.

These run on every PR regardless of LLM availability, are free, and have
verifiable evidence by construction (the matched line). They complement the
LLM security pass: rules catch well-known dangerous APIs precisely; the LLM
catches logic flaws (missing authorization, IDOR, ...) that patterns cannot.
"""
import re
from dataclasses import dataclass

from app.core.diff_parser import ChangedFile
from app.review.schemas import ReviewFinding


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    title: str
    severity: str
    explanation: str
    fix: str
    requires: re.Pattern | None = None   # extra condition on the same line


RULES = (
    Rule("SEC001", re.compile(r"(?<![\w.])(eval|exec)\s*\((?!\s*['\"][^'\"]*['\"]\s*\))"),
         "Dynamic code execution with eval/exec", "high",
         "eval/exec run arbitrary Python; with any user-influenced input this is remote code execution.",
         "Parse the data instead (json.loads, ast.literal_eval) or use an explicit dispatch table."),
    Rule("SEC002", re.compile(r"\bshell\s*=\s*True\b"),
         "Subprocess call with shell=True", "high",
         "shell=True passes the command through /bin/sh, so interpolated values enable command injection.",
         "Pass an argument list and keep shell=False: subprocess.run(['cmd', arg], check=True)."),
    Rule("SEC003", re.compile(r"\bos\.(system|popen)\s*\("),
         "Shell command via os.system/os.popen", "high",
         "os.system/os.popen invoke a shell; any interpolated value can inject commands.",
         "Use subprocess.run with an argument list and shell=False."),
    Rule("SEC004", re.compile(r"\b(pickle|cPickle|marshal|dill|shelve)\.(loads?|open)\s*\("),
         "Unsafe deserialization", "high",
         "Unpickling attacker-controlled bytes executes arbitrary code.",
         "Use a data-only format such as JSON, or sign and verify the payload before loading."),
    Rule("SEC005", re.compile(r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*(yaml\.)?C?SafeLoader)"),
         "yaml.load without SafeLoader", "medium",
         "yaml.load with the default/full loader can construct arbitrary Python objects.",
         "Use yaml.safe_load(...) or yaml.load(data, Loader=yaml.SafeLoader)."),
    Rule("SEC006", re.compile(r"\bverify\s*=\s*False\b"),
         "TLS certificate verification disabled", "medium",
         "Disabling certificate verification allows man-in-the-middle attacks.",
         "Remove verify=False; if a private CA is needed, pass verify='/path/to/ca.pem'."),
    Rule("SEC007", re.compile(
        r"""(?i)\b\w*(password|passwd|secret|api_?key|access_?token|auth_?token|private_?key)\w*\s*[:=]\s*['"][^'"\s]{8,}['"]"""),
         "Hardcoded credential", "high",
         "Secrets committed to source control leak to everyone with repository access and to history.",
         "Load the value from an environment variable or a secret manager and rotate the exposed secret."),
    Rule("SEC008", re.compile(
        r"""(?i)\.(execute|executemany|raw)\s*\(\s*(f['"]|['"][^'"]*['"]\s*(%|\+|\.format\b))"""),
         "SQL query built with string formatting", "high",
         "Formatting values into SQL text allows SQL injection.",
         "Use parameterized queries: cursor.execute('... WHERE id = %s', (user_id,))."),
    Rule("SEC009", re.compile(r"\bhashlib\.(md5|sha1)\s*\("),
         "Weak hash function", "low",
         "MD5/SHA-1 are broken for security purposes (passwords, signatures, tokens).",
         "Use hashlib.sha256 for integrity, and bcrypt/argon2/pbkdf2 for passwords."),
    Rule("SEC010", re.compile(r"\.run\s*\([^)]*\bdebug\s*=\s*True"),
         "Application started in debug mode", "medium",
         "Debug servers expose interactive debuggers / stack traces that allow code execution.",
         "Read the debug flag from configuration and keep it off in production."),
    Rule("SEC011", re.compile(r"\brandom\.(random|randint|randrange|choice|choices|getrandbits)\s*\("),
         "Non-cryptographic randomness for a secret", "medium",
         "The random module is predictable; tokens or passwords generated with it can be guessed.",
         "Use the secrets module (secrets.token_urlsafe, secrets.choice).",
         requires=re.compile(r"(?i)token|secret|password|otp|nonce|salt|session|key")),
    Rule("SEC012", re.compile(r"\btempfile\.mktemp\s*\("),
         "Insecure temporary file", "medium",
         "tempfile.mktemp has a race condition between name creation and file creation.",
         "Use tempfile.NamedTemporaryFile or tempfile.mkstemp."),
)

_PLACEHOLDER = re.compile(r"(?i)(your[_-]|example|changeme|placeholder|dummy|xxxx|<.*>|\*{4})")


def scan_file(changed: ChangedFile, sensitive: bool = False) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for line_no, line in sorted(changed.added_lines.items()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for rule in RULES:
            if not rule.pattern.search(line):
                continue
            if rule.requires and not rule.requires.search(line):
                continue
            if rule.id == "SEC007" and _PLACEHOLDER.search(line):
                continue
            findings.append(ReviewFinding(
                title=rule.title,
                description=f"{rule.title} on an added line.",
                category="security",
                severity=rule.severity,
                confidence=0.8,
                status="confirmed",
                source=f"rule:{rule.id}",
                file=changed.path,
                start_line=line_no,
                end_line=line_no,
                comment_line=line_no,
                evidence=stripped[:300],
                explanation=rule.explanation,
                suggested_fix=rule.fix,
                evidence_verified=True,
                in_changed_lines=True,
                sensitive_file=sensitive,
            ))
    return findings
