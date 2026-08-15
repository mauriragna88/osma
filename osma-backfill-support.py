import sqlite3, json, sys, io, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def main():
    db = sys.argv[1]
    mode = sys.argv[2]  # obs | existing
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    if mode == "obs":
        seen = {}
        rows = con.execute(
            "SELECT id, agent, type, content, quest_id, topic_key FROM observations "
            "WHERE topic_key LIKE 'atlas/quest-outcomes/%' ORDER BY id").fetchall()
        for r in rows:
            qid = r["quest_id"] or ""
            if not qid:
                m = re.search(r"Q-(\d+)", r["topic_key"] or "")
                if m:
                    qid = "Q-" + m.group(1)
            if not qid:
                m = re.search(r"Quest\s+(Q-?\d+)\s+verdict", r["content"] or "")
                if m:
                    qid = m.group(1)
            if not qid:
                continue
            verdict = ""
            m = re.search(r"verdict:\s*([A-Z_]+)", r["content"] or "")
            if m:
                verdict = m.group(1)
            seen[qid] = {"id": r["id"], "agent": r["agent"] or "atlas",
                         "quest": qid, "verdict": verdict, "content": r["content"]}
        print(json.dumps(list(seen.values()), ensure_ascii=False))
    elif mode == "existing":
        rows = con.execute(
            "SELECT DISTINCT quest_id FROM experiences "
            "WHERE quest_id IS NOT NULL AND quest_id != ''").fetchall()
        print(json.dumps([r[0] for r in rows], ensure_ascii=False))

if __name__ == "__main__":
    main()
