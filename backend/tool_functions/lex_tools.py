"""Lex (legal counsel) role-specific tools."""

from __future__ import annotations

import json
import re
from pathlib import Path

_TEXT_SUFFIXES = {
    ".php", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".py", ".twig",
    ".erb", ".astro", ".md",
}
_SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", "storage", "dist", "build", ".cat-crew-ai"}
_MAX_FILES = 4000
_MAX_BYTES = 400_000
_SAMPLES = 4

# Form fields that collect personal data (name / id / type of an input).
_FIELD = re.compile(
    r"""<(?:input|textarea|select)\b[^>]*?\b(?:name|type|id)\s*=\s*["']?"""
    r"""(email|e-mail|tel|phone|mobile|password|first_?name|last_?name|full_?name|name|address|street|city|zip|postcode|"""
    r"""birth|dob|birthday|age|gender|company|vat|iban|card|file|avatar|photo|location|ip)\b""",
    re.IGNORECASE,
)
# Third parties that receive visitor data, by what their snippet or SDK looks like.
_SERVICES: dict[str, re.Pattern[str]] = {
    "Google Analytics / Tag Manager": re.compile(r"googletagmanager\.com|google-analytics\.com|\bgtag\(|\bga\(['\"]create", re.I),
    "Meta (Facebook) Pixel": re.compile(r"connect\.facebook\.net|\bfbq\(", re.I),
    "Hotjar": re.compile(r"hotjar\.com|\bhj\(", re.I),
    "Microsoft Clarity": re.compile(r"clarity\.ms", re.I),
    "Mixpanel / Segment / Amplitude": re.compile(r"mixpanel|cdn\.segment\.com|amplitude\.com", re.I),
    "Google Fonts (visitor IP sent to Google)": re.compile(r"fonts\.googleapis\.com|fonts\.gstatic\.com", re.I),
    "Google reCAPTCHA": re.compile(r"google\.com/recaptcha|grecaptcha", re.I),
    "Google Maps": re.compile(r"maps\.googleapis\.com|google\.com/maps/embed", re.I),
    "YouTube / Vimeo embeds": re.compile(r"youtube\.com/embed|youtube-nocookie\.com|player\.vimeo\.com", re.I),
    "Stripe": re.compile(r"js\.stripe\.com|\bStripe\(|stripe/stripe-php|laravel/cashier", re.I),
    "PayPal": re.compile(r"paypal\.com/sdk|paypalobjects", re.I),
    "Mailchimp / newsletter": re.compile(r"mailchimp|list-manage\.com|sendinblue|brevo|mailerlite", re.I),
    "Intercom / chat widget": re.compile(r"intercom|tawk\.to|crisp\.chat|livechatinc", re.I),
    "Sentry (error reports)": re.compile(r"sentry\.io|@sentry/|sentry/sentry", re.I),
}
_COOKIES = re.compile(r"document\.cookie|setcookie\(|Cookie::(?:queue|make|forever)|->cookie\(|localStorage\.setItem|sessionStorage\.setItem", re.I)
# Packages in composer.json / package.json that mean personal data leaves the site or is stored.
_PACKAGES = re.compile(
    r"stripe|cashier|paypal|mollie|braintree|mailgun|sendgrid|postmark|aws-sdk|mailchimp|socialite|passport|sanctum|"
    r"sentry|bugsnag|analytics|pusher|twilio|vonage|firebase|onesignal|algolia|intercom",
    re.I,
)
_ENV_KEYS = re.compile(r"^(MAIL_\w+|STRIPE_\w+|PAYPAL_\w+|AWS_\w+|PUSHER_\w+|GOOGLE_\w+|FACEBOOK_\w+|GITHUB_\w+|TWILIO_\w+|SENTRY_\w+|MAILCHIMP_\w+|RECAPTCHA_\w+)\s*=", re.M)


def _files(root: Path):
    count = 0
    stack = [root]
    while stack and count < _MAX_FILES:
        folder = stack.pop()
        try:
            children = sorted(folder.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if child.name not in _SKIP_DIRS and not child.name.startswith("."):
                    stack.append(child)
            elif child.suffix.lower() in _TEXT_SUFFIXES or child.name.endswith(".blade.php"):
                count += 1
                yield child


def _note(found: dict[str, list[str]], key: str, where: str) -> None:
    hits = found.setdefault(key, [])
    if len(hits) < _SAMPLES and where not in hits:
        hits.append(where)
    elif len(hits) >= _SAMPLES and not hits[-1].startswith("..."):
        hits.append("...")


def data_collection_scan(path: str = ".") -> str:
    """Inventory what personal data a project collects and who receives it, from its source code.

    The facts a privacy policy, cookie notice or terms page has to match: form fields that
    collect personal data (email, phone, name, address, uploads...), third-party services that
    receive visitor data (analytics, pixels, fonts, embeds, payments, newsletters, chat widgets,
    error reporting), cookies and browser storage set by the code, data-related packages in
    composer.json / package.json, and integration keys named in .env.example. It never reads
    .env itself (secrets). Read-only.

    Args:
        path: Project folder to scan, relative to the workspace. Defaults to the project root.

    Returns:
        A grouped inventory with file:line samples, or an ``Error:`` message.
    """
    from .. import tools as _tools

    try:
        root = _tools.resolve_user_path(path or ".")
    except ValueError as exc:
        return f"Error: {exc}"
    if not root.is_dir():
        return f"Error: not a folder: {root}"

    fields: dict[str, list[str]] = {}
    services: dict[str, list[str]] = {}
    cookies: dict[str, list[str]] = {}
    scanned = 0
    for file in _files(root):
        try:
            if file.stat().st_size > _MAX_BYTES:
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        rel = file.relative_to(root).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for match in _FIELD.finditer(line):
                _note(fields, match.group(1).lower().replace("-", ""), f"{rel}:{number}")
            for label, pattern in _SERVICES.items():
                if pattern.search(line):
                    _note(services, label, f"{rel}:{number}")
            if _COOKIES.search(line):
                _note(cookies, "set by the code", f"{rel}:{number}")

    packages: list[str] = []
    for manifest, sections in (("composer.json", ("require", "require-dev")), ("package.json", ("dependencies", "devDependencies"))):
        try:
            data = json.loads((root / manifest).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for section in sections:
            for name in (data.get(section) or {}):
                if _PACKAGES.search(name):
                    packages.append(f"{name} ({manifest})")
    env_keys: list[str] = []
    try:
        env_keys = sorted(set(_ENV_KEYS.findall((root / ".env.example").read_text(encoding="utf-8", errors="replace"))))
    except OSError:
        pass

    def block(title: str, found: dict[str, list[str]]) -> list[str]:
        if not found:
            return [f"{title}: none found"]
        return [f"{title}:"] + [f"  - {key}: {', '.join(where)}" for key, where in sorted(found.items())]

    lines = [f"Personal-data inventory of {root} ({scanned} source files scanned)"]
    lines += block("Form fields collecting personal data", fields)
    lines += block("Third parties receiving visitor data", services)
    lines += block("Cookies / browser storage", cookies)
    lines.append("Data-related packages: " + (", ".join(sorted(set(packages))) or "none found"))
    lines.append("Integration keys in .env.example: " + (", ".join(env_keys) or "none found"))
    lines.append(
        "Every item above belongs in the privacy policy (what, why, legal basis, who receives it, "
        "how long). Non-essential cookies and trackers need consent before they load (GDPR / ePrivacy)."
    )
    return "\n".join(lines)
