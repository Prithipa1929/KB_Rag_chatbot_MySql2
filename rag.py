"""Question answering. Detailed, comprehensive point-by-point responses."""
import json
import logging
import re
import urllib.request
from cache import semantic_cache
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
from datetime import datetime
import config
import kb

log = logging.getLogger(__name__)

NOT_FOUND_MD = (
    "**Not available in KB**\n"
    "- I could not find this information in the company documents.\n"
    "- Please contact the HR or Admin support team for assistance."
)
LLM_NOT_FOUND = "NOT_FOUND"
MIN_SENT_SCORE = 0.015
MAX_BULLETS = 8

PROMPT = """You are the official virtual assistant for {company}.
"This company", "your company", "the company" always mean {company}.

Answer using ONLY the context below.

RULES:
1. If the context contains the answer, or closely related information
   (even if worded differently, e.g. "established by" for "founder"), answer using it.
2. If the context has only partial information, give what is available.
3. Reply EXACTLY {nf} ONLY if the context has nothing at all related to the question.
4. Do not invent facts that are not in the context.

FORMAT: Start with a short bold title, followed by bullet points starting with "- " where each bullet is a complete, natural sentence.
Context:
{context}

Question: {question}
Answer:"""


# ---------------- small talk ----------------
_GREET = {"hi", "hii", "hiii", "hello", "helo", "hey", "hola", "vanakkam", "vanakam", "namaste", "yo"}
_THANKS = {"thanks", "thank", "thankyou", "thx", "nandri", "nanri", "tq", "ty"}
_BYE = {"bye", "goodbye", "tata", "cya"}
_SORRY = {"sorry", "sry", "soory", "my bad", "apologies", "hoo sorry"}
_TOXIC_WORDS = {"mental", "idiot", "stupid", "fool", "shut up", "mad", "useless", "nonsense", "bloody", "hate you"}

def _time_period():
    h = datetime.now().hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    return "evening"

def smalltalk(question: str):
    toks = re.findall(r"[a-z']+", question.lower())
    if not toks:
        return None
    text = " ".join(toks)
    c = config.COMPANY_NAME

    if any(w in toks for w in _TOXIC_WORDS) or any(t in text for t in _TOXIC_WORDS):
        return (
            "**Notice:**\n"
            "- Please use professional and polite language.\n"
            f"- I am here to assist you with {c} policies, products, and processes."
        )

    if any(s in text for s in _SORRY):
        return (
            "**No worries at all!**\n"
            "- How can I help you today?\n"
            "- Feel free to ask any question about company policies."
        )

    if len(toks) <= 6:
        actual = _time_period()
        for said in ("morning", "afternoon", "evening", "night"):
            if f"good {said}" in text:
                if said != actual:
                    return (
                        f"**Good {actual.title()}!**\n"
                        f"- Sorry, it's actually {actual} right now, so good {actual}!\n"
                        f"- How can I help you with {c} today?"
                    )
                return (
                    f"**Good {actual.title()}!**\n"
                    f"- I am the {c} official virtual assistant.\n"
                    f"- How can I help you today?"
                )

        if toks[0] in _GREET:
            return f"**Hello!**\n- I am the {c} official virtual assistant.\n- How can I help you today?"
        if toks[0] in _THANKS or text.startswith("thank you"):
            return f"**You're welcome!**\n- Let me know if you need anything else about {c}."
        if toks[0] in _BYE:
            return "**Goodbye!**\n- Have a great day ahead."
        if text in ("how are you", "how are u", "how r u", "how are you doing"):
            return f"**I'm doing good!**\n- How can I help you with {c} today?"
        if text in ("who are you", "what are you", "what can you do", "help", "what do you do"):
            return (
                f"**About this Assistant**\n"
                f"- I am the official {c} virtual assistant, and I answer questions using verified company documents."
            )

    return None


# ---------------- helpers ----------------
def _label(h):
    return f"{h['source_file']} (p.{h['page_number']})" if h["page_number"] else h["source_file"]


def _parse_chunk(text: str):
    if text.startswith("## "):
        head, _, body = text[3:].partition("\n")
        return head.strip(), body
    return "", text


def _split_into_points(body: str):
    """Splits body text into individual statements so each item gets its own bullet point."""
    lines = []
    for block in body.splitlines():
        block = block.strip()
        if not block:
            continue
        subparts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])|(?<=\d)\s+(?=[A-Z][a-z]+)", block)
        for part in subparts:
            part = part.strip()
            if len(part) >= 10:
                lines.append(part)
    return lines


def _query_title(question: str):
    skip = ENGLISH_STOP_WORDS | {"tell", "please", "give", "show", "explain", "know", "want", "what", "how", "where"}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", question) if w.lower() not in skip]
    return " ".join(words).title() or "Policy Details"


def _gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.LLM_MODEL}:generateContent"
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    # Indha part-ai replace pannunga:
    gen_config = {
        "temperature": config.TEMPERATURE,
        "maxOutputTokens": 2048,
    }
    if "2.5" in config.LLM_MODEL:
        gen_config["thinkingConfig"] = {"thinkingBudget": 0}

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": safety_settings,
        "generationConfig": gen_config,
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": config.GOOGLE_API_KEY},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _extractive(question: str, hits):
    """Local fallback: returns all relevant details as distinct point-by-point bullets."""
    cands, seen = [], set()
    for h in hits:
        head, body = _parse_chunk(h["text"])
        for pt in _split_into_points(body):
            key = re.sub(r"\W+", " ", pt.lower()).strip()
            if len(key) < 10 or key in seen:
                continue
            seen.add(key)
            cands.append({"head": head, "text": pt, "hit": h})

    if not cands:
        return None

    s_scores = kb.score_texts(question, [c["text"] for c in cands])
    h_scores = kb.score_texts(question, [c["head"] or " " for c in cands])
    for c, s, hs in zip(cands, s_scores, h_scores):
        c["score"] = s + 0.4 * hs if c["head"] else s
        c["sent_score"] = s

    best = max((c["score"] for c in cands), default=0)
    if best < MIN_SENT_SCORE:
        # Still return top points from the highest-scoring chunk if chunks matched
        top_cands = cands[:MAX_BULLETS]
        title = top_cands[0]["head"] or _query_title(question)
        bullet_lines = [f"- {c['text']}" for c in top_cands]
        used_sources = sorted({_label(c["hit"]) for c in top_cands})
        return f"**{title}**\n" + "\n".join(bullet_lines), used_sources

    keep = [c for c in cands if c["score"] >= max(MIN_SENT_SCORE, 0.35 * best)]
    keep = sorted(keep, key=lambda c: -c["score"])[:MAX_BULLETS]

    if not keep:
        return None

    title = keep[0]["head"] or _query_title(question)
    bullet_lines = [f"- {c['text']}" for c in keep]
    used_sources = sorted({_label(c["hit"]) for c in keep})

    return f"**{title}**\n" + "\n".join(bullet_lines), used_sources

def _merge_hits(*hit_lists, limit=8):
    merged = {}
    for lst in hit_lists:
        for h in lst:
            key = (h["source_file"], h["text"])
            if key not in merged or h.get("score", 0) > merged[key].get("score", 0):
                merged[key] = h
    return sorted(merged.values(), key=lambda x: -x.get("score", 0))[:limit]


# ---------------- main entry ----------------
def ask(question: str):
    """Returns (answer, sources, found) with Semantic Caching."""
    talk = smalltalk(question)
    if talk:
        return talk, [], True

    cached_response = semantic_cache.get(question)
    if cached_response:
        answer, sources, found = cached_response
        return answer + "\n\n*(Answered instantly from Semantic Cache)*", sources, found

    # 3. Search with both corrected query and expanded query + keyword fallback
    corrected = kb.correct_query(question)
    expanded = kb.expand_query(corrected)

    hits_exact = kb.search(corrected, k=6)
    hits_exp = kb.search(expanded, k=6) if expanded != corrected else []
    hits = _merge_hits(hits_exact, hits_exp, limit=6)

    if not hits:
        hits = _merge_hits(hits, kb.keyword_search(corrected, k=4), limit=6)

    # 4. Gemini LLM Call
    answer, sources, found = None, [], False
    if config.GOOGLE_API_KEY:
        try:
            context = "\n\n".join(h["text"].replace("## ", "Section: ", 1) for h in hits)
            answer = _gemini(PROMPT.format(
                company=config.COMPANY_NAME,
                nf=LLM_NOT_FOUND,
                context=context,
                question=question,
            ))
            if LLM_NOT_FOUND in answer:
                return NOT_FOUND_MD, [], False            
            else:
                sources = sorted({_label(h) for h in hits})
                found = True
        except Exception as e:
            log.exception("Gemini call failed (%s); falling back to extractive answer", e)
            answer = None

    # 5. Local Extractive Fallback
    if not answer:
        result = _extractive(corrected, hits)
        if not result:
            return NOT_FOUND_MD, [], False
        answer, sources, found = result[0], result[1], True

    if found and answer:
        semantic_cache.put(question, answer, sources, found)

    return answer, sources, found