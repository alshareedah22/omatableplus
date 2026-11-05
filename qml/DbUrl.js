.pragma library

// Parse a database URL (postgres://…, mysql://…, redis://…, mongodb://…,
// sqlite:///…) into connection form fields. Returns
// { engine, host, port, user, password, database, path, tls, auth_source }
// with only the parts the URL names, or { error: "…" }.

var SCHEMES = {
  postgres: "postgresql", postgresql: "postgresql", pg: "postgresql", pgsql: "postgresql",
  mysql: "mysql", mariadb: "mariadb",
  redis: "redis", rediss: "redis", valkey: "valkey", valkeys: "valkey",
  mongodb: "mongodb", mongo: "mongodb",
  sqlite: "sqlite", sqlite3: "sqlite", file: "sqlite"
}

function decode(s) {
  try { return decodeURIComponent(s.replace(/\+/g, "%20")) } catch (e) { return s }
}

function decodePart(s) {
  try { return decodeURIComponent(s) } catch (e) { return s }
}

function query(qs) {
  var out = {}
  if (!qs) return out
  qs.split("&").forEach(function(pair) {
    if (!pair) return
    var i = pair.indexOf("=")
    var k = decode(i < 0 ? pair : pair.slice(0, i)).toLowerCase()
    out[k] = i < 0 ? "" : decode(pair.slice(i + 1))
  })
  return out
}

function truthy(v) { return /^(1|true|yes|on|require|required)$/i.test(v || "") }

// libpq sslmode, MySQL ssl-mode, ssl=true / tls=true
function tlsFrom(q) {
  var mode = (q.sslmode || q["ssl-mode"] || q.ssl_mode || "").toLowerCase()
  if (mode) {
    if (mode === "disable" || mode === "disabled") return "disable"
    if (mode === "allow" || mode === "prefer" || mode === "preferred") return "prefer"
    return "require"   // require, verify-ca, verify-full, verify_identity, …
  }
  var flag = q.ssl !== undefined ? q.ssl : q.tls
  if (flag === undefined) return ""
  return truthy(flag) ? "require" : /^(0|false|no|off|disable)$/i.test(flag) ? "disable" : ""
}

function parse(text) {
  var s = String(text || "").trim()
  // DATABASE_URL="postgres://…" pasted from an .env file
  s = s.replace(/^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*/, "").replace(/^(["'])(.*)\1$/, "$2").trim()
  s = s.replace(/^jdbc:/i, "")
  if (!s) return { error: "Paste a database URL" }

  var m = /^([A-Za-z][A-Za-z0-9+.\-]*):(.*)$/.exec(s)
  if (!m) return { error: "Not a URL: it should start with a scheme like postgres://" }
  var scheme = m[1].toLowerCase()
  var rest = m[2]
  if (scheme === "mongodb+srv")
    return { error: "mongodb+srv URLs need a DNS lookup; use a mongodb://host:port URL instead" }
  var base = scheme.split("+")[0]          // postgresql+psycopg, mysql+pymysql
  var engine = SCHEMES[base]
  if (!engine) return { error: "Unsupported scheme \"" + m[1] + "\"" }

  var out = { engine: engine }
  var hash = rest.indexOf("#")
  if (hash >= 0) rest = rest.slice(0, hash)
  var qi = rest.indexOf("?")
  var q = query(qi >= 0 ? rest.slice(qi + 1) : "")
  if (qi >= 0) rest = rest.slice(0, qi)

  if (engine === "sqlite") {
    // sqlite:///abs, sqlite:////abs, file:/abs, file:./rel, sqlite:~/x.db
    var p = decodePart(rest.replace(/^\/\/(localhost)?(?=\/)/, "").replace(/^\/\//, "/"))
    if (!p || p === "/") return { error: "The URL has no database file" }
    out.path = p
    return out
  }

  if (rest.slice(0, 2) !== "//") return { error: "The URL has no host: expected " + base + "://host" }
  rest = rest.slice(2)
  var slash = rest.indexOf("/")
  var authority = slash >= 0 ? rest.slice(0, slash) : rest
  var path = slash >= 0 ? rest.slice(slash + 1) : ""

  var at = authority.lastIndexOf("@")
  if (at >= 0) {
    var cred = authority.slice(0, at)
    authority = authority.slice(at + 1)
    var colon = cred.indexOf(":")
    var user = decodePart(colon >= 0 ? cred.slice(0, colon) : cred)
    if (user) out.user = user
    if (colon >= 0) out.password = decodePart(cred.slice(colon + 1))
  }

  // several hosts (replica sets, libpq failover): use the first
  var hostport = authority.split(",")[0]
  var hm = /^\[([^\]]*)\](?::(\d*))?$/.exec(hostport) || /^([^:]*)(?::(\d*))?$/.exec(hostport)
  if (!hm) return { error: "Could not read the host and port" }
  if (hm[1]) out.host = decodePart(hm[1])
  if (hm[2]) out.port = parseInt(hm[2], 10)

  var db = decodePart(path.replace(/\/+$/, ""))
  if (db) out.database = db

  // libpq lets everything come from the query string instead
  if (q.host && !out.host && q.host.charAt(0) !== "/") out.host = q.host
  if (q.port && !out.port) out.port = parseInt(q.port, 10) || undefined
  if ((q.user || q.username) && !out.user) out.user = q.user || q.username
  if (q.password !== undefined && out.password === undefined) out.password = q.password
  if ((q.dbname || q.database) && !out.database) out.database = q.dbname || q.database

  if (out.port !== undefined && !(out.port >= 1 && out.port <= 65535))
    return { error: "The port must be between 1 and 65535" }

  var tls = tlsFrom(q)
  if (scheme === "rediss" || scheme === "valkeys") tls = "require"
  if (tls) out.tls = tls
  if (engine === "redis" || engine === "valkey") {
    if (q.db && !out.database) out.database = q.db
    if (out.database && !/^\d+$/.test(out.database)) return { error: "The Redis database must be a number" }
  }
  if (engine === "mongodb" && q.authsource) out.auth_source = q.authsource
  return out
}
