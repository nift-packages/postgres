# postgres

PostgreSQL database package for Nift.

Runtime dependency: the `psql` executable on `PATH`. The package drives `psql`
through Nift's structured process API (argv, never shell concatenation).

## Installation

```text
nift add nift-packages/postgres
```

## Import

```text
@import("postgres")
```

## API

```text
postgres.available()                 // bool: psql on PATH and process execution enabled
postgres.version()                   // psql --version first line, or "" when unavailable
db := postgres.open({...})           // connection descriptor
postgres.exec(db, sql, ...params)    // {ok, rows, columns, error, error_code, exit_code}
rows := postgres.query(db, sql, ...params)   // {ok, rows, columns, error, error_code, exit_code}
postgres.transaction(db, statements) // {ok, error, error_code, exit_code}
```

`open` accepts `host`, `port`, `database`, `user`. These are passed to `psql` as
separate `-h`/`-p`/`-d`/`-U` options, so a value containing spaces cannot inject
additional libpq connection keywords. A `password` is deliberately not accepted:
set `PGPASSWORD` in the environment instead (the package never logs
credentials).

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

## Parameter binding

SQL parameters use PostgreSQL's positional `$1, $2, ...` placeholders. Binding
is **structural**, not quote-escaping:

- Strings are emitted as a hex bytea decoded to UTF-8
  (`convert_from(decode('..','hex'),'UTF8')`). String parameters are interpreted
  as Nift's UTF-8 bytes. This is independent of the server's
  `standard_conforming_strings` setting, and quotes, backslashes, newlines, tabs
  and Unicode are preserved byte-exactly.
- `null`, booleans and integer/float values are emitted as `NULL`, `true`/`false`
  and validated numeric literals.
- Array, object and other non-scalar parameters are rejected with
  `error_code: "invalid_parameters"`.

```text
rows := postgres.query(db, "SELECT * FROM posts WHERE views > $1", 10)
```

Placeholders are substituted only outside string literals (including
dollar-quoted `$tag$...$tag$` and `$$...$$` strings), quoted identifiers and
comments, and `$1` can never alter `$10`. This is still textual substitution,
**not** a database prepared statement: never assemble dynamic SQL from untrusted
fragments, bind scalar values only.

## Result shape

All operations return `{ok, error, error_code, exit_code}`; `query` also returns
`rows` and `columns`. `error_code` is `""` on a completed call,
`"invalid_parameters"` for a rejected parameter type, and `"backend_unavailable"`
when `psql` is missing or process execution is disabled. `exit_code` is `127` in
the unavailable case.

## Availability

`available()` is false when `psql` is missing or Nift runs with `--no-process`
(`NIFT_NO_PROCESS`). Operations then return a recoverable `backend_unavailable`
result without invoking the client.

## Limitations (v0.1.0)

- Requires a reachable PostgreSQL server with usable credentials for live
  queries; without one the package still supports discovery, argv construction
  and structured failures.
- Tab-separated result parsing: fields containing tabs are not representable.
- No password field; use `PGPASSWORD`.
- PostgreSQL text cannot contain NUL; a bound string containing NUL is passed
  through the structural form and is rejected by the server.
- `transaction()` takes raw SQL statements and performs no parameter binding;
  callers own any escaping for that operation.

## Tests

The deterministic suite uses a fake `psql` executable and needs no server:

```sh
python3 -B tests/test_postgres.py /path/to/nift
```

Version: 0.1.0
