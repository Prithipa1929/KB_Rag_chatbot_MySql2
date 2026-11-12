"""Guardrails for the chatbot.

1. check_input()  - blocks prompt-injection style messages before they reach the search / the LLM.
2. mask_pii()     - hides personal data (phone, email, card, Aadhaar, PAN ...) using Microsoft Presidio.
                    If Presidio or its spaCy model is not installed, simple regex rules are used instead,
                    so the app still starts.
3. guard_output() - last check on the answer: no leaked system prompt, no high-risk IDs.
"""
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)
logging.getLogger("tldextract").setLevel(logging.CRITICAL)  # Presidio's email check tries a download when offline

BLOCKED_ANSWER = ("**I can't help with that**\n"
                  "- That message tries to change my rules or reach private data.\n"
                  "- Please ask a question about company policies, products or processes.")


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


# ---------------- 1. input guard ----------------
_INJECTION = re.compile("|".join([
    r"(ignore|disregard|forget)\W+(all\W+|any\W+|the\W+|your\W+|everything\W+)*(previous|above|prior|earlier|system)?\W*(instructions?|rules|prompts?|guidelines)",
    r"(reveal|show|print|repeat|display|leak|tell me)\W+(me\W+)?(your\W+|the\W+)?(system|hidden|initial|secret)\W+(prompt|instructions?|message)",
    r"\byou are now\b|\bpretend (to be|you are)\b|\bact as (a|an|if)\b",
    r"developer mode|jailbreak|do anything now|\bDAN\b",
    r"(bypass|override|disable)\W+(the\W+|your\W+)?(rules|guardrails?|filters?|security|restrictions?)",
    r"(dump|list|show|give|send)\W+(me\W+)?all\W+(the\W+)?(documents|files|passwords|users|database|api keys?|secrets?)",
]), re.I)


def check_input(text: str) -> Verdict:
    if _INJECTION.search(text or ""):
        return Verdict(False, "prompt injection")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text or ""):
        return Verdict(False, "control characters")
    return Verdict(True)


# ---------------- 2. PII masking ----------------
# Names and places are NOT masked: people ask "who is Divya Menon?" and the answer is in the documents.
_INPUT_ENTITIES = ["PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IN_AADHAAR", "IN_PAN",
                   "IN_PASSPORT", "IBAN_CODE", "IP_ADDRESS"]
# In ANSWERS only high-risk IDs are hidden: the company's own phone / email / HR name are meant to be shown.
_OUTPUT_ENTITIES = ["CREDIT_CARD", "IN_AADHAAR", "IN_PAN", "IN_PASSPORT", "IBAN_CODE"]

# Presidio gives every match a confidence score. Weak guesses (for example 0.01 for "hr@zentora") would corrupt the
# company's own phone / email in answers, so answers use a higher minimum than questions.
_MIN_SCORE_INPUT = 0.05
_MIN_SCORE_OUTPUT = 0.3

def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# A bare 12-digit number is masked as Aadhaar even when its checksum is wrong (Presidio would skip a typo).
_AADHAAR_LIKE = (re.compile(r"(?<!\d)(?<!\d )\d{4} ?\d{4} ?\d{4}(?! ?\d)"), "<IN_AADHAAR>")
_PAN_STRICT = (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "<IN_PAN>")            # a bare PAN scores only 0.1 in Presidio
_CARD_LIKE = (re.compile(r"(?<!\d)\d(?:[ -]?\d){12,15}(?!\d)"),             # only real card numbers (checksum passes)
              lambda m: "<CREDIT_CARD>" if _luhn_ok(m.group()) else m.group())
_ALWAYS = [_AADHAAR_LIKE, _PAN_STRICT]                                         # applied on top of Presidio
_REGEX_INPUT = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL_ADDRESS>"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), "<PHONE_NUMBER>"),
    _CARD_LIKE, _AADHAAR_LIKE, _PAN_STRICT,
]
_REGEX_OUTPUT = [_CARD_LIKE, _AADHAAR_LIKE, _PAN_STRICT]

_analyzer = _anonymizer = None


def _load_presidio():
    global _analyzer, _anonymizer
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_analyzer.predefined_recognizers import InAadhaarRecognizer, InPanRecognizer, InPassportRecognizer
        from presidio_anonymizer import AnonymizerEngine

        nlp = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]})
        _analyzer = AnalyzerEngine(nlp_engine=nlp.create_engine(), supported_languages=["en"])
        for rec in (InAadhaarRecognizer(), InPanRecognizer(), InPassportRecognizer()):  # Indian IDs are off by default
            _analyzer.registry.add_recognizer(rec)
        _anonymizer = AnonymizerEngine()
        log.info("Presidio loaded: PII masking is active")
    except Exception as e:  # not installed, or the spaCy model is missing
        _analyzer = _anonymizer = None
        log.warning("Presidio not available (%s). Using simple regex masking instead.", e)


_load_presidio()


def mask_pii(text: str, output: bool = False) -> str:
    """'my phone is 98765 43210' -> 'my phone is <PHONE_NUMBER>'."""
    if not text:
        return text
    if _analyzer is not None:
        try:
            found = _analyzer.analyze(text=text, language="en",
                                      entities=_OUTPUT_ENTITIES if output else _INPUT_ENTITIES,
                                      score_threshold=_MIN_SCORE_OUTPUT if output else _MIN_SCORE_INPUT)
            text = _anonymizer.anonymize(text=text, analyzer_results=found).text
            rules = _ALWAYS
        except Exception:
            log.exception("Presidio failed; using regex masking")
            rules = _REGEX_OUTPUT if output else _REGEX_INPUT
    else:
        rules = _REGEX_OUTPUT if output else _REGEX_INPUT
    for pattern, label in rules:
        text = pattern.sub(label, text)
    return text


# ---------------- 3. output guard ----------------
def guard_output(answer: str) -> str:
    if "official assistant for" in answer and "Use ONLY the context" in answer:   # the system prompt leaked
        return BLOCKED_ANSWER
    return mask_pii(answer, output=True)