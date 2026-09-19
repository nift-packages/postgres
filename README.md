# postgres

PostgreSQL database package for Nift.

Runtime dependency: the `psql` executable on `PATH`. The package drives `psql`
through Nift's structured process API (argv, never shell concatenation). The
v0.1.0 backend may change later without requiring consumers to rewrite around
it.

## Installation

```text
nift add nift-packages/postgres
```

## Import

```text
@import("postgres")
```

## API

The exported `postgres` struct:

```text
postgres.available()                 // bool: psql on PATH
postgres.version()                   // psql --version first line
db := postgres.open({...})           // connection descriptor
postgres.exec(db, sql, ...params)    // {ok, error, exit_code}
rows := postgres.query(db, sql, ...params)   // {ok, rows, columns, error, exit_code}
postgres.transaction(db, statements) // {ok, error, exit_code}
```

`open` accepts `host`, `port`, `database`, `user`. A `password` is deliberately
not accepted: set `PGPASSWORD` in the environment instead (the package never
logs credentials).

```text
@import("postgres")

db := postgres.open({
    "host": "localhost",
    "port": 5432,
    "database": "example",
    "user": "nick"
})
print(postgres.exec(db, "CREATE TABLE posts(id INTEGER, title TEXT)").ok)
rows := postgres.query(db, "SELECT * FROM posts")
for (row : rows.rows) {
    print(row.title)
}
```

`query` runs `psql` in unaligned mode and parses the result into `columns` (array
of names) and `rows` (array of objects). The v0.1.0 parser uses tab-separated
output; values containing tabs are a documented limitation.

SQL parameters use PostgreSQL's positional `$1, $2, ...` placeholders, bound by
the package into `psql` literals:

```text
rows := postgres.query(db, "SELECT * FROM posts WHERE views > $1", 10)
```

## Result shape

All operations return `{ok, error, exit_code}`; `query` also returns `rows` and
`columns`. `exit_code` is `127` when `psql` is missing.

## Limitations (v0.1.0)

- Requires a reachable PostgreSQL server with usable credentials for live
  queries; without one the package still supports discovery, argv construction
  and structured failures.
- Tab-separated result parsing: fields containing tabs are not representable.
- No password field; use `PGPASSWORD`.

Version: 0.1.0