#!/usr/bin/env python3
"""Deterministic security and contract tests for the postgres Nift package.

Runs against a fake `psql` executable so no server, credentials or network are
required. The fake records its exact argv so generated SQL and process argv can
be inspected without touching a database.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

NIFT = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/nick/Repositories/nift/nift/nift").resolve()
PACKAGE = Path(__file__).resolve().parent.parent
TESTS = PACKAGE / "tests"
PACKAGE_SOURCE = PACKAGE / "src" / "postgres.f"

manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
if manifest != {"name": "postgres", "version": "0.1.0", "entry": "src/postgres.f", "description": "PostgreSQL database package"}:
    raise SystemExit(f"FAIL unexpected package manifest: {manifest!r}")

source = PACKAGE_SOURCE.read_text(encoding="utf-8")
exports = re.findall(r"^export\(([^)]+)\)$", source, re.MULTILINE)
public_methods = re.findall(r"^    fn\(([A-Za-z_][A-Za-z0-9_]*)\(", source, re.MULTILINE)
expected_public = ["available", "version", "open", "exec", "query", "transaction"]
if exports != ["postgres"] or public_methods != expected_public:
    raise SystemExit(f"FAIL unexpected public surface: exports={exports!r} methods={public_methods!r}")

FAKE = r"""#!/usr/bin/env bash
n=0
if [ -f "${PG_CAPTURE}.count" ]; then n=$(cat "${PG_CAPTURE}.count"); fi
dir="${PG_CAPTURE}.call${n}"
mkdir -p "$dir"
i=0
for a in "$@"; do printf '%s' "$a" > "$dir/$i"; i=$((i+1)); done
echo $((n+1)) > "${PG_CAPTURE}.count"
for a in "$@"; do
  if [ "$a" = "--version" ]; then echo "psql (PostgreSQL) 18.0 (Fake)"; exit 0; fi
done
unaligned=0
for a in "$@"; do
  if [ "$a" = "-A" ]; then unaligned=1; fi
done
if [ "$unaligned" = "1" ]; then
  printf 'id\tname\n1\tAda\n2\tLin\n'
fi
exit 0
"""


def require(condition, message, result=None):
    if condition:
        return
    if result is not None:
        message += f"\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}\nreturn code: {result.returncode}"
    raise SystemExit("FAIL " + message)


def run(args, cwd, env):
    return subprocess.run(
        [str(NIFT), *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def base_env(bin_dir, capture):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["PG_CAPTURE"] = str(capture)
    env.pop("NIFT_NO_PROCESS", None)
    return env


def make_fake(bin_dir):
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "psql"
    fake.write_text(FAKE, encoding="utf-8")
    fake.chmod(0o755)
    return fake


def clear_capture(capture):
    for path in Path(capture).parent.glob(Path(capture).name + "*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def calls(capture):
    parsed = []
    index = 0
    while True:
        directory = Path(f"{capture}.call{index}")
        if not directory.is_dir():
            break
        args = []
        i = 0
        while (directory / str(i)).exists():
            args.append((directory / str(i)).read_text(encoding="utf-8"))
            i += 1
        parsed.append(args)
        index += 1
    return parsed


def execute_sql(capture):
    for argv in reversed(calls(capture)):
        if "-c" in argv:
            return argv[argv.index("-c") + 1]
    return None


def hex_literal(text):
    return "convert_from(decode('" + text.encode("utf-8").hex().upper() + "','hex'),'UTF8')"


def nift_string(text):
    if text == "":
        return '""'
    return "bytes([" + ",".join(str(b) for b in text.encode("utf-8")) + ']).decode("utf-8")'


WORK = Path(tempfile.mkdtemp(prefix=".postgres-tests-", dir=TESTS))
try:
    consumer = WORK / "consumer"
    consumer.mkdir()
    (consumer / ".nift").mkdir()
    bin_dir = WORK / "bin"
    make_fake(bin_dir)

    added = run(["add", str(PACKAGE)], consumer, base_env(bin_dir, WORK / "add.txt"))
    require(added.returncode == 0, "nift add into fresh consumer failed", added)

    def run_script(name, body, env=None, capture="cap.txt"):
        capfile = WORK / capture
        clear_capture(capfile)
        (consumer / name).write_text(body, encoding="utf-8")
        return run([name], consumer, env or base_env(bin_dir, capfile))

    # 1. Connection parameters are separate argv and cannot inject conninfo keywords.
    cap = WORK / "conn.txt"
    body = """@import("postgres")
db := postgres.open({"host":"evil.example sslmode=disable","port":5432,"database":"example","user":"nick"})
print(postgres.exec(db, "SELECT 1").ok.to_string())
"""
    res = run_script("conn.f", body, capture="conn.txt")
    require(res.returncode == 0, "conninfo test failed", res)
    argv = calls(cap)[0]
    require(argv[:8] == ["-h", "evil.example sslmode=disable", "-p", "5432", "-U", "nick", "-d", "example"],
            f"connection argv not structural: {argv!r}")

    # 1b. Hostile values stay exactly one argv element each; no conninfo breakout,
    # no shell interpretation, regardless of spaces/= /quotes/backslashes/newlines.
    hostile = {
        "host": "h = 'x' \\ path\nnew sslmode=require service=foo options=-c $(touch "
                + str(WORK / "pg-pwned") + ") `id`",
        "user": "u;\t\"quoted\"\\x",
        "database": "db\nname = injected",
    }
    pwn = WORK / "pg-pwned"
    cap = WORK / "conn2.txt"
    body = ('@import("postgres")\n'
            'db := postgres.open({"host":' + nift_string(hostile["host"]) + ',"port":5432,'
            '"user":' + nift_string(hostile["user"]) + ',"database":' + nift_string(hostile["database"]) + '})\n'
            'print(postgres.exec(db, "SELECT 1").ok.to_string())\n')
    res = run_script("conn2.f", body, capture="conn2.txt")
    require(res.returncode == 0, "hostile conninfo test failed", res)
    argv = calls(cap)[0]
    require(argv[:8] == ["-h", hostile["host"], "-p", "5432", "-U", hostile["user"], "-d", hostile["database"]],
            f"hostile connection argv not structural: {argv!r}")
    require(not pwn.exists(), "connection value caused a shell side effect")

    # 2. Basic query: deterministic result shape and structural binding.
    cap = WORK / "basic.txt"
    body = """@import("postgres")
db := postgres.open({"user":"nick","database":"test"})
r := postgres.query(db, "SELECT id, name FROM t WHERE name = $1", "Ada")
print("shape=" + r.ok.to_string() + "," + type(r.rows) + "," + r.error_code + "," + r.exit_code.to_string())
print("cols=" + r.columns.size().to_string() + ":" + r.columns[0])
print("rows=" + r.rows.size().to_string())
"""
    res = run_script("basic.f", body, capture="basic.txt")
    require(res.returncode == 0, "basic query failed", res)
    require("shape=true,array,,0" in res.stdout and "cols=2:id" in res.stdout and "rows=2" in res.stdout,
            f"query result unexpected: {res.stdout!r}")
    require(execute_sql(cap) == "SELECT id, name FROM t WHERE name = " + hex_literal("Ada"),
            f"basic bind mismatch: {execute_sql(cap)!r}")

    # 3. Adversarial parameter matrix.
    payloads = [
        "'", "\\", "\\'", '\\"', ";", "--", "/*", "*/", "\n", "\r\n", "\t",
        "Unicode: é😀", "x'; DROP TABLE t; --", "x\\'; DROP TABLE t; --",
        "1 OR 1=1", "''''", "\\\\", "$1", "$nift$", "$$", "a\tb\nc", "\x00",
    ]
    for index, payload in enumerate(payloads):
        cap = WORK / f"adv{index}.txt"
        body = (
            '@import("postgres")\n'
            "db := postgres.open({})\n"
            'r := postgres.query(db, "SELECT $1", ' + nift_string(payload) + ")\n"
            "print(r.ok.to_string())\n"
        )
        res = run_script(f"adv{index}.f", body, capture=f"adv{index}.txt")
        require(res.returncode == 0, f"adversarial payload {payload!r} failed", res)
        sql = execute_sql(cap)
        require(sql == "SELECT " + hex_literal(payload), f"payload {payload!r} not structurally bound: {sql!r}")

    # 4. Placeholders inside literals, identifiers and comments are not substituted.
    cap = WORK / "ctx.txt"
    body = """@import("postgres")
db := postgres.open({})
r := postgres.query(db, "SELECT '$1' AS a, \\"$2\\" AS b, 1 -- $3\\n /* $4 */ , $5", "P", "Q", "R", "S", "T")
print(r.ok.to_string())
"""
    res = run_script("ctx.f", body, capture="ctx.txt")
    require(res.returncode == 0, "context query failed", res)
    sql = execute_sql(cap)
    for untouched in ["'$1'", '"$2"', "-- $3", "/* $4 */"]:
        require(untouched in sql, f"placeholder inside {untouched} was substituted: {sql!r}")
    require(sql.rstrip().endswith(hex_literal("T")), f"final outside placeholder not bound: {sql!r}")

    # 4b. PostgreSQL dollar-quoted strings are opaque to placeholder substitution.
    dollar_cases = [
        ("SELECT $$ literal $1 $$, $1", "SELECT $$ literal $1 $$, " + hex_literal("V")),
        ("SELECT $tag$ literal $1 $tag$, $1", "SELECT $tag$ literal $1 $tag$, " + hex_literal("V")),
        ("SELECT $nift$ $1 $nift$", "SELECT $nift$ $1 $nift$"),
        ("SELECT $1, $$x$$", "SELECT " + hex_literal("V") + ", $$x$$"),
        ("SELECT $outer$ a $inner$ $1 $outer$, $1", "SELECT $outer$ a $inner$ $1 $outer$, " + hex_literal("V")),
        ("SELECT '$$ $1 $$', $1", "SELECT '$$ $1 $$', " + hex_literal("V")),
        ("SELECT $a1_b$ $1 $a1_b$, $1", "SELECT $a1_b$ $1 $a1_b$, " + hex_literal("V")),
    ]
    for index, (sql, expected) in enumerate(dollar_cases):
        cap = WORK / f"dollar{index}.txt"
        body = ('@import("postgres")\ndb := postgres.open({})\n'
                'r := postgres.query(db, ' + nift_string(sql) + ', "V")\nprint(r.ok.to_string())\n')
        res = run_script(f"dollar{index}.f", body, capture=f"dollar{index}.txt")
        require(res.returncode == 0, f"dollar-quote case {index} failed: {sql!r}", res)
        require(execute_sql(cap) == expected, f"dollar-quote case {index}: expected {expected!r} got {execute_sql(cap)!r}")
    # A `$n` that is not followed by a digit and not a dollar-quote opener is literal.
    cap = WORK / "dollar_lit.txt"
    body = ('@import("postgres")\ndb := postgres.open({})\n'
            'r := postgres.query(db, ' + nift_string("SELECT $foo bar, $1") + ', "V")\nprint(r.ok.to_string())\n')
    res = run_script("dollar_lit.f", body, capture="dollar_lit.txt")
    require(res.returncode == 0 and execute_sql(cap) == "SELECT $foo bar, " + hex_literal("V"),
            f"non-opener $ literal mishandled: {execute_sql(cap)!r}")

    # 5. Backslash-escaped quote inside a user string must not expose a placeholder.
    cap = WORK / "esc.txt"
    body = """@import("postgres")
db := postgres.open({})
r := postgres.query(db, "SELECT 'a\\\\' $1' AS note", "x'; DROP TABLE t; --")
print(r.ok.to_string())
"""
    res = run_script("esc.f", body, capture="esc.txt")
    require(res.returncode == 0, "backslash context query failed", res)
    sql = execute_sql(cap)
    require("$1" in sql, f"placeholder inside backslash-escaped string was substituted: {sql!r}")

    # 6. $10 must not be confused with $1, scalar forms, unsupported types.
    cap = WORK / "ten.txt"
    body = """@import("postgres")
db := postgres.open({})
postgres.exec(db, "INSERT INTO t VALUES ($1, $2, $3, $4, $5, $10)", "t", 42, 1.5, true, null, "a", "b", "c", "d", "z")
"""
    res = run_script("ten.f", body, capture="ten.txt")
    require(res.returncode == 0, "scalar/$10 query failed", res)
    sql = execute_sql(cap)
    require(sql == "INSERT INTO t VALUES (" + hex_literal("t") + ", 42, 1.5, true, NULL, " + hex_literal("z") + ")",
            f"scalar/$10 literals wrong: {sql!r}")

    for typename, literal in [("array", "[1, 2]"), ("object", '{"a": 1}')]:
        res = run_script(f"bad_{typename}.f",
                         '@import("postgres")\n'
                         "db := postgres.open({})\n"
                         'r := postgres.exec(db, "SELECT $1", ' + literal + ")\n"
                         "print(r.error_code)\n",
                         capture=f"bad_{typename}.txt")
        require(res.returncode == 0, f"{typename} parameter raised instead of returning", res)
        require(res.stdout.strip() == "invalid_parameters", f"{typename} parameter not rejected structurally", res)

    # 7. transaction() passes raw statements with ON_ERROR_STOP and no binding.
    cap = WORK / "tx.txt"
    body = """@import("postgres")
db := postgres.open({})
r := postgres.transaction(db, ["INSERT INTO a VALUES (1)", "INSERT INTO b VALUES (2)"])
print(r.ok.to_string() + ":" + r.error_code)
"""
    res = run_script("tx.f", body, capture="tx.txt")
    require(res.returncode == 0 and res.stdout.strip() == "true:", "transaction result wrong", res)
    tx_argv = calls(cap)[0]
    require(execute_sql(cap) == "BEGIN;INSERT INTO a VALUES (1);INSERT INTO b VALUES (2);COMMIT;",
            f"transaction SQL wrong: {execute_sql(cap)!r}")
    require("ON_ERROR_STOP=1" in tx_argv, f"transaction missing ON_ERROR_STOP: {tx_argv!r}")

    # Large parameter counts are supported without recursion limits.
    count = 100
    args = ", ".join(nift_string(f"p{i}") for i in range(count))
    cap = WORK / "many.txt"
    body = ('@import("postgres")\ndb := postgres.open({})\npostgres.query(db, "SELECT $1, $' + str(count) + '", ' + args + ")\n")
    res = run_script("many.f", body, capture="many.txt")
    require(res.returncode == 0, "100-parameter query failed", res)
    require(execute_sql(cap) == "SELECT " + hex_literal("p0") + ", " + hex_literal(f"p{count - 1}"),
            f"100-parameter binding mismatch: {execute_sql(cap)!r}")

    # 8. Deterministic repeatability.
    body = """@import("postgres")
db := postgres.open({})
postgres.query(db, "SELECT $1, $2", "x\\'; --", 7)
"""
    (consumer / "det.f").write_text(body, encoding="utf-8")
    cap_a, cap_b = WORK / "det_a.txt", WORK / "det_b.txt"
    for capfile in (cap_a, cap_b):
        clear_capture(capfile)
        r = run(["det.f"], consumer, base_env(bin_dir, capfile))
        require(r.returncode == 0, "determinism run failed", r)
    require(execute_sql(cap_a) == execute_sql(cap_b), "binding is not deterministic")

    # 8. Structured argv: shell metacharacters are inert.
    pwn = WORK / "pwned"
    payload = "$(touch " + str(pwn) + ") `touch " + str(pwn) + "` ; touch " + str(pwn) + " && echo"
    cap = WORK / "shell.txt"
    body = ('@import("postgres")\ndb := postgres.open({})\npostgres.query(db, "SELECT $1", ' + nift_string(payload) + ")\n")
    res = run_script("shell.f", body, capture="shell.txt")
    require(res.returncode == 0, "shell-metacharacter payload failed", res)
    require(not pwn.exists(), "payload caused shell side effect")
    require(execute_sql(cap) == "SELECT " + hex_literal(payload), "shell payload not bound structurally")

    # 9. --no-process and executable unavailable.
    noproc = base_env(bin_dir, WORK / "noproc.txt")
    noproc["NIFT_NO_PROCESS"] = "1"
    clear_capture(WORK / "noproc.txt")
    body = """@import("postgres")
db := postgres.open({})
print("available=" + postgres.available().to_string())
r := postgres.query(db, "SELECT $1", "x")
print("ok=" + r.ok.to_string() + " code=" + r.error_code)
"""
    res = run_script("noproc.f", body, env=noproc, capture="noproc.txt")
    require(res.returncode == 0, "--no-process raised instead of returning", res)
    require("available=false" in res.stdout and "ok=false code=backend_unavailable" in res.stdout,
            f"--no-process result wrong: {res.stdout!r}")
    require(calls(WORK / "noproc.txt") == [], "--no-process still invoked the executable")

    empty_bin = WORK / "emptybin"
    empty_bin.mkdir()
    unavailable = dict(os.environ)
    unavailable["PATH"] = str(empty_bin)
    unavailable["PG_CAPTURE"] = str(WORK / "unavailable.txt")
    unavailable.pop("NIFT_NO_PROCESS", None)
    body = """@import("postgres")
db := postgres.open({})
print("available=" + postgres.available().to_string())
r := postgres.query(db, "SELECT 1")
print("ok=" + r.ok.to_string() + " code=" + r.error_code + " exit=" + r.exit_code.to_string())
"""
    res = run_script("unavailable.f", body, env=unavailable, capture="unavailable.txt")
    require(res.returncode == 0, "unavailable executable raised instead of returning", res)
    require("available=false" in res.stdout and "ok=false code=backend_unavailable exit=127" in res.stdout,
            f"unavailable result wrong: {res.stdout!r}")

    # 10. Privacy.
    for helper in ["bind", "literal", "hex_encode", "string_literal", "prepare", "cli_exec", "cli_query",
                   "process_available", "unavailable", "parse_rows"]:
        (consumer / "privacy.f").write_text(f'@import("postgres")\npostgres.{helper}(1, 2)\n', encoding="utf-8")
        res = run(["privacy.f"], consumer, base_env(bin_dir, WORK / "privacy.txt"))
        require(res.returncode != 0, f"private helper {helper} was accessible")

finally:
    shutil.rmtree(WORK, ignore_errors=True)

print("PASS postgres package security tests")
