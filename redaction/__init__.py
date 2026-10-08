"""Redaction between stored applications and the LLM.

The LLM only ever receives redacted text. Officers see the original via
restore(). Detection is local (Presidio + spaCy + regex); no cloud PII
service and no LLM is used to redact. SYNTHETIC DATA ONLY until a privacy
impact assessment is complete.
"""
