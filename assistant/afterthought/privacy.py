"""What never gets in, and what gets cut out of whatever does.

Consent is enforced here, at the watcher, before a single character is read:

- **Denied apps and windows** (password managers, banking, private/incognito windows)
  are skipped before their title or text is looked at, so no memory of them is ever
  made that would later need deleting.
- **Per-app consent**: an app is only watched once you have opted it in (by voice or
  on the Memory screen). Off means off.
- **Secret stripping** runs on everything that is kept: passwords, card numbers,
  one-time codes, API keys and tokens become "[redacted]" before anything is embedded.
"""

from __future__ import annotations

import re

DENY_APPS = {
    "keepass", "keepassxc", "1password", "bitwarden", "lastpass", "dashlane", "enpass", "nordpass",
    "keeper", "roboform", "credentialuibroker", "consent", "lockapp", "logonui", "windows security",
    "securityhealthsystray", "authy", "winauth",
}
DENY_TITLES = [
    r"\bincognito\b", r"\binprivate\b", r"private browsing", r"\bpassword", r"\bpasscode\b",
    r"\bsign[ -]?in\b", r"\blog[ -]?in\b", r"\botp\b", r"\bnet ?banking\b", r"\binternet banking\b",
    r"\b(?:sbi|hdfc|icici|axis|kotak|pnb)\b.*\bbank", r"\bupi\b", r"\bpaytm\b", r"\bphonepe\b",
    r"\bgoogle pay\b", r"\bcheckout\b", r"\bpayment\b", r"\bcredit card\b", r"\bwallet\b",
    r"\b2fa\b", r"two-factor", r"\bverification code\b", r"\.env\b", r"\bsecrets?\b", r"\bprivate key\b",
]
_DENY_TITLE = re.compile("|".join(DENY_TITLES), re.I)

_SECRETS = [
    # key = value pairs that name a secret
    (re.compile(r"(?i)\b(pass(?:word|wd|code)?|pwd|secret|token|api[_ -]?key|auth|bearer|private[_ -]?key)"
                r"\b\s*[:=]\s*\S+"), r"\1: [redacted]"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{12,}=*"), "Bearer [redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted key]"),
    # Well-known token shapes
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"), "[redacted]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[redacted]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[redacted jwt]"),
    # Card numbers (13-19 digits, spaced or not), then Aadhaar-shaped numbers
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[redacted number]"),
    (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), "[redacted number]"),
    # One-time codes said as such
    (re.compile(r"(?i)\b(otp|one[- ]time (?:password|code)|verification code|security code|cvv|pin)\b"
                r"(\D{0,20})\d{3,8}\b"), r"\1\2[redacted]"),
    # Long random-looking strings (40+ chars of base64/hex): almost always a key or hash
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "[redacted]"),
]


def denied(process: str, title: str, deny_apps: set[str] | None = None, deny_titles: list[str] | None = None) -> bool:
    name = (process or "").lower().removesuffix(".exe").strip()
    if name in DENY_APPS or name in {a.lower() for a in (deny_apps or set())}:
        return True
    if title and _DENY_TITLE.search(title):
        return True
    return any(re.search(pattern, title or "", re.I) for pattern in (deny_titles or []))


def redact(text: str) -> str:
    """Strip anything secret-shaped. Applied before storing or embedding, always."""
    out = text or ""
    for pattern, replacement in _SECRETS:
        out = pattern.sub(replacement, out)
    return out
