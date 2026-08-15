import sqlite3, json, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    db = sys.argv[1]
    con = sqlite3.connect(db)

    def c(sql, *p):
        try:
            return con.execute(sql, p).fetchone()[0]
        except Exception:
            return 0

    meta = {}
    try:
        for k, v in con.execute("SELECT key, value FROM meta").fetchall():
            meta[k] = v
    except Exception:
        pass

    print(json.dumps({
        "schema": meta.get("schema_version", 0),
        "obs": c("SELECT COUNT(*) FROM observations"),
        "links": c("SELECT COUNT(*) FROM observation_links"),
        "exps": c("SELECT COUNT(*) FROM experiences"),
        "cues": c("SELECT COUNT(*) FROM experience_cues"),
        "patterns": c("SELECT COUNT(*) FROM patterns"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
