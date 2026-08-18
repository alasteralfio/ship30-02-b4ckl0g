"""Create the reference-store database in data/.

Run:  python -m pipeline.init_db

Idempotent — safe to re-run. It creates missing tables and leaves existing
data untouched, then prints the schema so it doubles as an inspect command.
Directory creation lives here, in the pipeline, not in the shared access layer:
a missing data/ means the reference data hasn't been built, and only the
offline pipeline is allowed to build it.
"""

from b4cklog import store


def main() -> None:
    store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with store.connect() as conn:
        store.init_db(conn)
        print(f"Reference store ready at {store.DB_PATH}\n")
        for table in store.table_names(conn):
            columns = ", ".join(store.column_names(conn, table))
            print(f"  {table} ({columns})")


if __name__ == "__main__":
    main()
