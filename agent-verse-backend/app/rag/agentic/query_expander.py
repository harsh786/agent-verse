"""QueryExpander — generates query variants for multi-source Fusion RAG."""
from __future__ import annotations


class QueryExpander:
    def expand(self, query: str, max_variants: int = 3) -> list[str]:
        variants = [query]
        q = query.lower()
        if "ticket" in q or "issue" in q:
            variants.append(q.replace("ticket", "issue").replace("issue", "ticket"))
        if "find" in q:
            variants.append(q.replace("find", "search for"))
        return list(dict.fromkeys(variants))[:max_variants]

    def expand_for_fusion(self, query: str, max_variants: int = 4) -> list[str]:
        """Generate multiple query phrasings for Fusion RAG (RRF across multiple queries)."""
        import re

        variants = [query]
        syns = [
            ("authentication", "login auth"),
            ("flow", "process workflow"),
            ("error", "exception failure"),
            ("deploy", "release launch"),
        ]
        q_lower = query.lower()
        for original, synonyms in syns:
            if original in q_lower:
                for syn in synonyms.split():
                    variants.append(re.sub(original, syn, q_lower, flags=re.I))
        stopwords = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or"}
        keywords = " ".join(w for w in query.split() if w.lower() not in stopwords)
        if keywords != query:
            variants.append(keywords)
        return list(dict.fromkeys(v for v in variants if v.strip()))[:max_variants]
