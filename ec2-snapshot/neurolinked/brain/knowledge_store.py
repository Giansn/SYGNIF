"""
NeuroLinked Knowledge Store

SQLite-based knowledge database that stores actual text, facts, and observations
alongside the neural simulation. This is the memory system that makes the brain
useful as a real knowledge management tool.

Every input to the brain (from Claude, dashboard, screen, etc.) gets stored here
with full text, timestamps, source, and auto-extracted tags. Knowledge can be
searched, recalled by topic, and retrieved by Claude via MCP tools.
"""

import os
import sys
import re
import math
import sqlite3
import time
import json
import threading
from collections import Counter


def _app_root():
    """Project/exe directory for storing brain_state/ next to the app."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Common English stop words to filter out of TF-IDF
STOP_WORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'but', 'if', 'then', 'is', 'are', 'was', 'were',
    'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'can', 'i', 'you', 'he', 'she', 'it',
    'we', 'they', 'what', 'which', 'who', 'when', 'where', 'why', 'how', 'this',
    'that', 'these', 'those', 'my', 'your', 'his', 'her', 'its', 'our', 'their',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'up', 'about',
    'into', 'through', 'during', 'before', 'after', 'above', 'below', 'between',
    'out', 'off', 'over', 'under', 'again', 'further', 'as', 'so', 'than', 'too',
    'very', 'just', 'only', 'also', 'not', 'no', 'nor', 'own', 'same', 'such',
    'there', 'here', 'some', 'any', 'all', 'each', 'few', 'more', 'most', 'other',
    'am', 'me', 'him', 'us', 'them',
})


def tokenize(text):
    """Lowercase + extract word tokens (no stop words, min 2 chars)."""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z0-9_']+", text.lower())
    return [w for w in words if len(w) >= 2 and w not in STOP_WORDS]


class KnowledgeStore:
    """Persistent knowledge database using SQLite with full-text search."""

    def __init__(self, db_path=None):
        if db_path is None:
            brain_state_dir = os.path.join(_app_root(), "brain_state")
            os.makedirs(brain_state_dir, exist_ok=True)
            db_path = os.path.join(brain_state_dir, "knowledge.db")

        self.db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            c = conn.cursor()

            # Main knowledge table
            c.execute("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    source TEXT DEFAULT 'unknown',
                    tags TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    neural_fingerprint TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    access_count INTEGER DEFAULT 0,
                    last_access REAL DEFAULT 0,
                    strength REAL DEFAULT 1.0
                )
            """)

            # Backfill columns for older databases (migration)
            for col, coldef in [
                ("access_count", "INTEGER DEFAULT 0"),
                ("last_access", "REAL DEFAULT 0"),
                ("strength", "REAL DEFAULT 1.0"),
            ]:
                try:
                    c.execute(f"ALTER TABLE knowledge ADD COLUMN {col} {coldef}")
                except sqlite3.OperationalError:
                    pass  # Column already exists

            # Token index for TF-IDF semantic search (real associative memory)
            c.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_tokens (
                    entry_id INTEGER NOT NULL,
                    token TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    FOREIGN KEY (entry_id) REFERENCES knowledge(id) ON DELETE CASCADE
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_tokens_token ON knowledge_tokens(token)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tokens_entry ON knowledge_tokens(entry_id)")

            # Global token frequency for IDF calculation
            c.execute("""
                CREATE TABLE IF NOT EXISTS token_stats (
                    token TEXT PRIMARY KEY,
                    doc_count INTEGER NOT NULL DEFAULT 0
                )
            """)

            # Full-text search index
            try:
                c.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                    USING fts5(text, tags, source, content=knowledge, content_rowid=id)
                """)
            except sqlite3.OperationalError:
                # FTS5 not available, fall back to basic LIKE queries
                self._fts_available = False
            else:
                self._fts_available = True

            # Triggers to keep FTS in sync
            if self._fts_available:
                c.execute("""
                    CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
                        INSERT INTO knowledge_fts(rowid, text, tags, source)
                        VALUES (new.id, new.text, new.tags, new.source);
                    END
                """)
                c.execute("""
                    CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
                        INSERT INTO knowledge_fts(knowledge_fts, rowid, text, tags, source)
                        VALUES ('delete', old.id, old.text, old.tags, old.source);
                    END
                """)

            conn.commit()
            conn.close()

    def store(self, text: str, source: str = "unknown", tags: list = None,
              neural_fingerprint: dict = None, metadata: dict = None) -> int:
        """
        Store a piece of knowledge.

        Args:
            text: The full text content to store
            source: Where it came from (claude, user, dashboard, screen)
            tags: Optional list of topic tags (auto-extracted if not provided)
            neural_fingerprint: Which brain regions fired when processing this
            metadata: Any additional metadata

        Returns:
            The ID of the stored entry
        """
        if not text or not text.strip():
            return -1

        if tags is None:
            tags = self._auto_extract_tags(text)

        tags_str = ",".join(tags) if tags else ""
        fp_str = json.dumps(neural_fingerprint) if neural_fingerprint else ""
        meta_str = json.dumps(metadata) if metadata else "{}"

        # Tokenize for TF-IDF semantic index
        tokens = tokenize(text)
        token_counts = Counter(tokens)

        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            c = conn.cursor()
            now = time.time()
            c.execute("""
                INSERT INTO knowledge (text, source, tags, timestamp, neural_fingerprint, metadata,
                                      access_count, last_access, strength)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1.0)
            """, (text.strip(), source, tags_str, now, fp_str, meta_str, now))
            entry_id = c.lastrowid

            # Index tokens for semantic search
            if token_counts:
                c.executemany(
                    "INSERT INTO knowledge_tokens (entry_id, token, count) VALUES (?, ?, ?)",
                    [(entry_id, tok, cnt) for tok, cnt in token_counts.items()]
                )
                # Update global token frequency (for IDF)
                for tok in token_counts:
                    c.execute("""
                        INSERT INTO token_stats (token, doc_count) VALUES (?, 1)
                        ON CONFLICT(token) DO UPDATE SET doc_count = doc_count + 1
                    """, (tok,))

            conn.commit()
            conn.close()

        return entry_id

    def semantic_search(self, query: str, limit: int = 10) -> list:
        """
        Real semantic search using TF-IDF cosine similarity.
        Finds conceptually related memories, not just keyword matches.

        This is actual associative memory — you can search "what I learned about
        customers" and find notes that never use those exact words.
        """
        if not query or not query.strip():
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        query_counts = Counter(query_tokens)

        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # Total doc count for IDF
            c.execute("SELECT COUNT(*) FROM knowledge")
            total_docs = max(c.fetchone()[0], 1)

            # Get IDF for query terms
            idf = {}
            for tok in query_counts:
                c.execute("SELECT doc_count FROM token_stats WHERE token = ?", (tok,))
                row = c.fetchone()
                doc_count = row[0] if row else 0
                # Smoothed IDF: log((N+1)/(df+1)) + 1
                idf[tok] = math.log((total_docs + 1) / (doc_count + 1)) + 1.0

            # Find candidate documents (any doc containing at least one query token)
            placeholders = ",".join("?" * len(query_counts))
            c.execute(f"""
                SELECT DISTINCT entry_id FROM knowledge_tokens
                WHERE token IN ({placeholders})
            """, list(query_counts.keys()))
            candidate_ids = [row[0] for row in c.fetchall()]

            if not candidate_ids:
                conn.close()
                return []

            # Score each candidate with cosine similarity
            scores = {}
            # Query vector magnitude
            q_mag = math.sqrt(sum((cnt * idf.get(tok, 0)) ** 2
                                  for tok, cnt in query_counts.items()))

            # Batch fetch tokens for candidates
            id_placeholders = ",".join("?" * len(candidate_ids))
            c.execute(f"""
                SELECT entry_id, token, count FROM knowledge_tokens
                WHERE entry_id IN ({id_placeholders})
            """, candidate_ids)

            doc_vectors = {}
            for entry_id, tok, cnt in c.fetchall():
                doc_vectors.setdefault(entry_id, {})[tok] = cnt

            for entry_id, doc_tokens in doc_vectors.items():
                # Get doc IDF values (use query IDFs where available, compute for others)
                # For cosine similarity we need doc magnitude over its OWN tokens
                doc_mag_sq = 0.0
                for tok, cnt in doc_tokens.items():
                    # Compute IDF for this token
                    if tok in idf:
                        tok_idf = idf[tok]
                    else:
                        c.execute("SELECT doc_count FROM token_stats WHERE token = ?", (tok,))
                        row = c.fetchone()
                        doc_count = row[0] if row else 1
                        tok_idf = math.log((total_docs + 1) / (doc_count + 1)) + 1.0
                    doc_mag_sq += (cnt * tok_idf) ** 2
                doc_mag = math.sqrt(doc_mag_sq) if doc_mag_sq > 0 else 1.0

                # Dot product over shared tokens
                dot = 0.0
                for tok, qcnt in query_counts.items():
                    if tok in doc_tokens:
                        dot += (qcnt * idf[tok]) * (doc_tokens[tok] * idf[tok])

                similarity = dot / (q_mag * doc_mag) if (q_mag * doc_mag) > 0 else 0
                scores[entry_id] = similarity

            # Boost by memory strength (accessed memories rank higher)
            top_ids = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit * 2]

            # Fetch full records for top candidates
            if not top_ids:
                conn.close()
                return []

            top_id_list = [i for i, _ in top_ids]
            top_id_placeholders = ",".join("?" * len(top_id_list))
            c.execute(f"""
                SELECT * FROM knowledge WHERE id IN ({top_id_placeholders})
            """, top_id_list)
            records = {row["id"]: dict(row) for row in c.fetchall()}

            # Build ranked results with combined score
            results = []
            for entry_id, similarity in top_ids:
                if entry_id in records:
                    rec = records[entry_id]
                    strength = rec.get("strength", 1.0) or 1.0
                    final_score = similarity * (0.7 + 0.3 * min(strength, 3.0))
                    rec["_similarity"] = round(similarity, 4)
                    rec["_score"] = round(final_score, 4)
                    results.append(rec)

            results.sort(key=lambda r: r["_score"], reverse=True)
            results = results[:limit]

            # Update access counts (memory strengthening through recall)
            if results:
                now = time.time()
                for r in results:
                    c.execute("""
                        UPDATE knowledge
                        SET access_count = access_count + 1,
                            last_access = ?,
                            strength = MIN(strength + 0.1, 5.0)
                        WHERE id = ?
                    """, (now, r["id"]))
                conn.commit()

            conn.close()

        return self._format_results(results)

    def associate(self, text: str, limit: int = 5) -> list:
        """
        Find memories associated with the given text.
        Used by the brain to auto-recall related content when new input arrives.
        """
        return self.semantic_search(text, limit=limit)

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """
        Sanitize a user query for FTS5 MATCH syntax.
        Strips special operators, wraps each word as a prefix token.
        e.g. "AI tools" -> "AI* tools*"
        """
        # Remove FTS5 special characters that break queries
        cleaned = re.sub(r'["\'\(\)\-\+\*\^\~\{\}\[\]:;!@#\$%&=<>,\./\\]', ' ', query)
        # Split into words, drop empties
        words = [w.strip() for w in cleaned.split() if w.strip()]
        if not words:
            return ""
        # Each word becomes a prefix search token joined with implicit AND
        return " ".join(f'"{w}"*' for w in words)

    def search(self, query: str, limit: int = 20) -> list:
        """
        Full-text search across all stored knowledge.

        Args:
            query: Search terms
            limit: Max results to return

        Returns:
            List of matching entries
        """
        if not query or not query.strip():
            return []

        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            if self._fts_available:
                # Sanitize query for FTS5 syntax
                fts_query = self._sanitize_fts_query(query)
                if fts_query:
                    try:
                        c.execute("""
                            SELECT k.* FROM knowledge k
                            JOIN knowledge_fts fts ON k.id = fts.rowid
                            WHERE knowledge_fts MATCH ?
                            ORDER BY rank
                            LIMIT ?
                        """, (fts_query, limit))
                    except sqlite3.OperationalError:
                        # Fall back to LIKE
                        c.execute("""
                            SELECT * FROM knowledge
                            WHERE text LIKE ? OR tags LIKE ?
                            ORDER BY timestamp DESC
                            LIMIT ?
                        """, (f"%{query}%", f"%{query}%", limit))
                else:
                    # Query was all special chars — fall back to LIKE
                    c.execute("""
                        SELECT * FROM knowledge
                        WHERE text LIKE ? OR tags LIKE ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    """, (f"%{query}%", f"%{query}%", limit))
            else:
                c.execute("""
                    SELECT * FROM knowledge
                    WHERE text LIKE ? OR tags LIKE ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (f"%{query}%", f"%{query}%", limit))

            results = [dict(row) for row in c.fetchall()]
            conn.close()

        return self._format_results(results)

    def recall(self, topic: str, limit: int = 10) -> list:
        """
        Recall knowledge about a specific topic.
        Searches text, tags, and source fields.

        Args:
            topic: Topic to recall
            limit: Max results

        Returns:
            List of relevant entries, most recent first
        """
        if not topic or not topic.strip():
            return []

        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # Search across text and tags
            words = topic.strip().split()
            conditions = []
            params = []
            for word in words:
                conditions.append("(text LIKE ? OR tags LIKE ?)")
                params.extend([f"%{word}%", f"%{word}%"])

            where_clause = " AND ".join(conditions)
            c.execute(f"""
                SELECT * FROM knowledge
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
            """, params + [limit])

            results = [dict(row) for row in c.fetchall()]
            conn.close()

        return self._format_results(results)

    def recent(self, limit: int = 20) -> list:
        """Get the most recent knowledge entries."""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT * FROM knowledge
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            results = [dict(row) for row in c.fetchall()]
            conn.close()

        return self._format_results(results)

    def get_by_source(self, source: str, limit: int = 50) -> list:
        """Get knowledge entries from a specific source."""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT * FROM knowledge
                WHERE source = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (source, limit))
            results = [dict(row) for row in c.fetchall()]
            conn.close()

        return self._format_results(results)

    def get_tags(self) -> list:
        """Get all unique tags with counts."""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            c = conn.cursor()
            c.execute("SELECT tags FROM knowledge WHERE tags != ''")
            rows = c.fetchall()
            conn.close()

        tag_counts = {}
        for row in rows:
            for tag in row[0].split(","):
                tag = tag.strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return sorted(
            [{"tag": t, "count": c} for t, c in tag_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )

    def get_stats(self) -> dict:
        """Get knowledge store statistics."""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            c = conn.cursor()

            c.execute("SELECT COUNT(*) FROM knowledge")
            total = c.fetchone()[0]

            c.execute("SELECT COUNT(DISTINCT source) FROM knowledge")
            sources = c.fetchone()[0]

            c.execute("SELECT source, COUNT(*) FROM knowledge GROUP BY source")
            by_source = {row[0]: row[1] for row in c.fetchall()}

            c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM knowledge")
            row = c.fetchone()
            first_entry = row[0]
            last_entry = row[1]

            conn.close()

        return {
            "total_entries": total,
            "sources": by_source,
            "unique_sources": sources,
            "first_entry": first_entry,
            "last_entry": last_entry,
            "tags": self.get_tags()[:20],
            "db_path": self.db_path,
        }

    def delete(self, entry_id: int) -> bool:
        """Delete a knowledge entry by ID."""
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30)
            c = conn.cursor()
            c.execute("DELETE FROM knowledge WHERE id = ?", (entry_id,))
            deleted = c.rowcount > 0
            conn.commit()
            conn.close()
        return deleted

    def _auto_extract_tags(self, text: str) -> list:
        """Auto-extract topic tags from text."""
        text_lower = text.lower()
        tags = []

        # Extract capitalized words as potential topics/names (2+ chars)
        caps = re.findall(r'\b([A-Z][a-zA-Z]{2,})\b', text)
        for word in caps:
            if word.lower() not in {'the', 'this', 'that', 'with', 'from', 'have',
                                     'been', 'will', 'they', 'their', 'about', 'would',
                                     'could', 'should', 'which', 'where', 'there',
                                     'when', 'what', 'your', 'here', 'just', 'also',
                                     'into', 'some', 'than', 'then', 'very', 'each',
                                     'make', 'like', 'does', 'made', 'after', 'before'}:
                tags.append(word.lower())

        # Extract quoted phrases
        quotes = re.findall(r'"([^"]+)"', text)
        tags.extend([q.lower() for q in quotes if len(q) < 50])

        # Extract hashtags
        hashtags = re.findall(r'#(\w+)', text)
        tags.extend([h.lower() for h in hashtags])

        # Deduplicate while preserving order
        seen = set()
        unique_tags = []
        for tag in tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        return unique_tags[:10]

    def _format_results(self, results: list) -> list:
        """Format database results for API responses."""
        formatted = []
        for r in results:
            entry = {
                "id": r["id"],
                "text": r["text"],
                "source": r["source"],
                "tags": [t.strip() for t in r["tags"].split(",") if t.strip()] if r["tags"] else [],
                "timestamp": r["timestamp"],
                "age": self._format_age(r["timestamp"]),
            }
            # Preserve semantic search scoring fields when present
            if "_score" in r:
                entry["score"] = r["_score"]
            if "_similarity" in r:
                entry["similarity"] = r["_similarity"]
            if "strength" in r and r["strength"] is not None:
                entry["strength"] = round(r["strength"], 3)
            if "access_count" in r and r["access_count"] is not None:
                entry["access_count"] = r["access_count"]
            formatted.append(entry)
        return formatted

    @staticmethod
    def _format_age(timestamp):
        """Format a timestamp as human-readable age."""
        if not timestamp:
            return "unknown"
        age = time.time() - timestamp
        if age < 60:
            return f"{int(age)}s ago"
        elif age < 3600:
            return f"{int(age / 60)}m ago"
        elif age < 86400:
            return f"{int(age / 3600)}h ago"
        else:
            return f"{int(age / 86400)}d ago"
