import re


_PATTERNS = (
    (re.compile(r'(?i)\b(password|contraseña|contrasena)\s*[:=]\s*\S+'), r'\1: [REDACTED]'),
    (re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b'), '[EMAIL]'),
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[PHONE]'),
    (re.compile(r'\b(?:\d[ -]*?){13,19}\b'), '[PAYMENT_NUMBER]'),
)


def redact_content(value):
    content = str(value or '')
    for pattern, replacement in _PATTERNS:
        content = pattern.sub(replacement, content)
    return content
