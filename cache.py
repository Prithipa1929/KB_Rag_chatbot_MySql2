"""Semantic Cache for fast, zero-cost responses using Cosine Similarity."""
import re
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in ENGLISH_STOP_WORDS}


class SemanticCache:
    def __init__(self, threshold=0.85):
        self.threshold = threshold
        self.entries = []
        self.questions = []
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
        self.tfidf_matrix = None

    def get(self, query: str):
        if not self.entries:
            return None

        try:
            query_vec = self.vectorizer.transform([query])
            scores = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
            best_idx = scores.argmax()
            best_score = scores[best_idx]

            if best_score >= self.threshold:
                match = self.entries[best_idx]
                q_words = _content_words(query)
                m_words = _content_words(match["question"])
                union = q_words | m_words
                jaccard = (len(q_words & m_words) / len(union)) if union else 0.0
                if jaccard >= 0.70:
                    print(f"[CACHE HIT] Score: {best_score:.2f} | Jaccard: {jaccard:.2f} | Matched: '{match['question']}'")
                    return match["answer"], match["sources"], match["found"]
        except Exception as e:
            print("[CACHE ERROR]", e)

        return None

    def put(self, question: str, answer: str, sources: list, found: bool):
        if not found or not answer:
            return

        self.entries.append({
            "question": question,
            "answer": answer,
            "sources": sources,
            "found": found,
        })
        self.questions.append(question)
        self.tfidf_matrix = self.vectorizer.fit_transform(self.questions)
        print(f"[CACHE STORED] Total cached questions: {len(self.entries)}")


semantic_cache = SemanticCache()