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
    bound := ""
    i := 0
    len := sql.length()
    state := 0
    while(i < len) {
        c := sql.substr(i, 1)
        n := ""
        if(i + 1 < len) { n = sql.substr(i + 1, 1) }
        if(state == 0) {
            if(c == "'") { bound += c; state = 1 }
            else if(c == "\"") { bound += c; state = 2 }
            else if(c == "-" && n == "-") { bound += "--"; i += 1; state = 3 }
            else if(c == "/" && n == "*") { bound += "/*"; i += 1; state = 4 }
            else if(c == "$") {
                j := i + 1
                digits := ""
                while(j < len) {
                    d := sql.substr(j, 1)
                    if(d == "0" || d == "1" || d == "2" || d == "3" || d == "4" || d == "5" || d == "6" || d == "7" || d == "8" || d == "9") { digits += d; j += 1 }
                    else { break }
                }
                if(digits == "") { bound += "$" }
                else {
                    num := digits.to_int()
                    if(num >= 1 && num <= params.size()) {
                        literal := postgres_literal(params[num - 1])
                        if(literal == null) { return null }
                        bound += literal
                    } else {
                        bound += "$" + digits
                    }
                    i = j - 1
                }
            }
            else { bound += c }
        }
        else if(state == 1) {
            bound += c
            if(c == "'") {
                if(n == "'") { bound += n; i += 1 }
                else { state = 0 }
            }
        }
        else if(state == 2) { bound += c; if(c == "\"") { state = 0 } }
        else if(state == 3) { bound += c; if(c == "\n") { state = 0 } }
        else if(state == 4) {
            bound += c
            if(c == "*" && n == "/") { bound += n; i += 1; state = 0 }
        }
        i += 1
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
    sql := "BEGIN;"
    for(statement : statements) { sql += statement + ";" }
    sql += "COMMIT;"
    if(!postgres_available()) { return {"ok":false,"error":"psql executable not found","exit_code":127} }
    result := run("psql", db.conn, "-q", "-v", "ON_ERROR_STOP=1", "-c", sql)
    if(result.exit_code != 0) { return {"ok":false,"error":result.stderr,"exit_code":result.exit_code} }
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
