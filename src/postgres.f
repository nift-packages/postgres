/*
    PostgreSQL database package for Nift. v0.1.0 backend: the psql executable.
    Public API: the exported `postgres` struct. Helpers stay private.
    Live-server semantics are verified against a reachable PostgreSQL; without
    a configured server this package still supports discovery, argv building,
    structured failures and private/export isolation.
*/

fn(postgres_available()) { return which("psql") != null }

fn(postgres_version_text()) {
    r := run("psql", "--version")
    if(r.exit_code != 0) { return "" }
    return r.stdout.trim()
}

fn(postgres_literal(value)) {
    t := type(value)
    if(t == "null") { return "NULL" }
    if(t == "bool") { if(value) { return "true" } return "false" }
    if(t == "int" || t == "float") { return value.to_string() }
    if(t == "string") { return "'" + value.replace("'", "''") + "'" }
    return null
}

fn(postgres_bind(sql, params)) {
    bound := sql
    i := 1
    for(p : params) {
        literal := postgres_literal(p)
        if(literal == null) { return null }
        bound = bound.replace("$" + i.to_string(), literal)
        i++
    }
    return bound
}

fn(postgres_conn_string(desc)) {
    if(desc == null) { return "" }
    host := desc.get("host", "localhost")
    port := desc.get("port", 5432)
    database := desc.get("database", "")
    user := desc.get("user", "")
    conn := "host=" + host + " port=" + port.to_string()
    if(database != "") { conn += " dbname=" + database }
    if(user != "") { conn += " user=" + user }
    return conn
}

fn(postgres_open_desc(desc)) {
    return {"conn": postgres_conn_string(desc)}
}

fn(postgres_cli_exec(db, sql, params)) {
    bound := postgres_bind(sql, params)
    if(bound == null) { return {"ok":false,"rows":[],"columns":[],"error":"parameter count/type mismatch","exit_code":2} }
    result := run("psql", db.conn, "-q", "-c", bound)
    return {"ok":result.exit_code == 0,"rows":[],"columns":[],"error":result.stderr,"exit_code":result.exit_code}
}

fn(postgres_parse_rows(text)) {
    columns := []
    rows := []
    lines := text.split("\n")
    if(lines.size() == 0) { return {"columns": columns, "rows": rows} }
    header := lines[0]
    columns = header.split("\t")
    i := 1
    while(i < lines.size()) {
        line := lines[i]
        if(line != "") {
            cells := line.split("\t")
            entries := []
            j := 0
            while(j < columns.size()) {
                val := ""
                if(j < cells.size()) { val = cells[j] }
                entries.push({"key": columns[j], "value": val})
                j++
            }
            rows.push(entries.from_entries())
        }
        i++
    }
    return {"columns": columns, "rows": rows}
}

fn(postgres_cli_query(db, sql, params)) {
    bound := postgres_bind(sql, params)
    if(bound == null) { return {"ok":false,"rows":[],"columns":[],"error":"parameter count/type mismatch","exit_code":2} }
    result := run("psql", db.conn, "-q", "-A", "-F", "\t", "-c", bound)
    if(result.exit_code != 0) { return {"ok":false,"rows":[],"columns":[],"error":result.stderr,"exit_code":result.exit_code} }
    parsed := postgres_parse_rows(result.stdout)
    return {"ok":true,"rows":parsed.rows,"columns":parsed.columns,"error":"","exit_code":0}
}

fn(postgres_cli_transaction(db, statements)) {
    i := 0
    while(i < statements.size()) {
        e := postgres_cli_exec(db, statements[i], [])
        if(!e.ok) { return {"ok":false,"error":e.error,"exit_code":e.exit_code} }
        i++
    }
    return {"ok":true,"error":"","exit_code":0}
}

@struct(postgres_api) {
    available := () => postgres_available()
    version := () => postgres_version_text()
    open := (desc) => postgres_open_desc(desc)
    exec := (db, sql, ...params) => { if(!postgres_available()) { return {"ok":false,"rows":[],"columns":[],"error":"psql executable not found","exit_code":127} }; return postgres_cli_exec(db, sql, params) }
    query := (db, sql, ...params) => { if(!postgres_available()) { return {"ok":false,"rows":[],"columns":[],"error":"psql executable not found","exit_code":127} }; return postgres_cli_query(db, sql, params) }
    transaction := (db, statements) => { if(!postgres_available()) { return {"ok":false,"error":"psql executable not found","exit_code":127} }; return postgres_cli_transaction(db, statements) }
}

postgres := postgres_api()
export(postgres)
