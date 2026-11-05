// Run by tests/test_complete.py with node.
const assert = require("assert")
const parse = (() => {
  const fs = require("fs"), path = require("path")
  const src = fs.readFileSync(path.join(__dirname, "../../qml/DbUrl.js"), "utf8").replace(/^\.pragma library\s*$/m, "")
  return new Function(src + "\nreturn parse")()
})()
const eq = (url, want) => assert.deepStrictEqual(parse(url), want, url)
const err = (url) => assert.ok(parse(url).error, url)

eq("postgres://alice:s%40cret@db.example.com:6543/app?sslmode=require",
  { engine: "postgresql", user: "alice", password: "s@cret", host: "db.example.com", port: 6543, database: "app", tls: "require" })
// certificate verification is not done: say so instead of quietly downgrading
eq("postgres://h/d?sslmode=verify-full", { engine: "postgresql", host: "h", database: "d", tls: "require",
  warnings: ["The URL asks to verify the server certificate (verify-full): OmaTablePlus encrypts the connection (TLS require) but does not check the certificate"] })
// a literal plus is a plus
eq("postgres://h/d?user=u&password=a+b", { engine: "postgresql", host: "h", database: "d", user: "u", password: "a+b" })
eq("mysql://u:a+b@h/d", { engine: "mysql", user: "u", password: "a+b", host: "h", database: "d" })
// a Unix socket directory is PostgreSQL's host
eq("postgresql:///d?host=/var/run/postgresql", { engine: "postgresql", database: "d", host: "/var/run/postgresql" })
err("mysql:///d?host=/run/mysqld")
eq("postgresql+psycopg://u@localhost/x", { engine: "postgresql", user: "u", host: "localhost", database: "x" })
eq("DATABASE_URL=\"postgresql://h/d?sslmode=disable\"", { engine: "postgresql", host: "h", database: "d", tls: "disable" })
eq("export DATABASE_URL='postgres://h'", { engine: "postgresql", host: "h" })
eq("postgresql:///mydb?host=db1&port=5433&user=bob&password=pw",
  { engine: "postgresql", database: "mydb", host: "db1", port: 5433, user: "bob", password: "pw" })
eq("postgresql://h1:5432,h2:5433/d", { engine: "postgresql", host: "h1", port: 5432, database: "d" })
eq("postgres://[::1]:5432/d", { engine: "postgresql", host: "::1", port: 5432, database: "d" })
eq("jdbc:mysql://root:p:w@127.0.0.1:3307/shop?ssl-mode=REQUIRED",
  { engine: "mysql", user: "root", password: "p:w", host: "127.0.0.1", port: 3307, database: "shop", tls: "require" })
eq("mysql+pymysql://u:@h/d", { engine: "mysql", user: "u", password: "", host: "h", database: "d" })
eq("mariadb://h", { engine: "mariadb", host: "h" })
eq("redis://:pw@cache:6380/2", { engine: "redis", password: "pw", host: "cache", port: 6380, database: "2" })
eq("rediss://default:pw@cache", { engine: "redis", user: "default", password: "pw", host: "cache", tls: "require" })
eq("valkey://h?db=3", { engine: "valkey", host: "h", database: "3" })
eq("mongodb://u:p@m1:27017,m2:27017/app?authSource=admin&tls=true",
  { engine: "mongodb", user: "u", password: "p", host: "m1", port: 27017, database: "app", auth_source: "admin", tls: "require" })
// without authSource, credentials authenticate against the URL's database
eq("mongodb://u:p@m/app", { engine: "mongodb", user: "u", password: "p", host: "m", database: "app", auth_source: "app" })
eq("mongodb://m/app", { engine: "mongodb", host: "m", database: "app" })
eq("sqlite:////home/me/app.db", { engine: "sqlite", path: "/home/me/app.db" })
// SQLAlchemy: three slashes are a relative path, which the app cannot resolve
eq("sqlite:///app.db", { engine: "sqlite", path: "app.db",
  warnings: ["The URL names a relative path (app.db): set the full path to the file"] })
eq("sqlite:~/my%20db.sqlite", { engine: "sqlite", path: "~/my db.sqlite" })
eq("sqlite:/tmp/x.db", { engine: "sqlite", path: "/tmp/x.db" })
eq("file:///tmp/x.db", { engine: "sqlite", path: "/tmp/x.db" })
eq("file:/tmp/x.db?mode=ro", { engine: "sqlite", path: "/tmp/x.db", readonly: true })
eq("sqlite:////tmp/x.db?immutable=1", { engine: "sqlite", path: "/tmp/x.db", readonly: true })
err("")
err("db.example.com:5432")
err("oracle://h/x")
err("mongodb+srv://cluster.example.net/app")
err("postgres://h:99999/x")
err("postgres://h:0/x")
err("postgres:///x?host=h&port=5432junk")
err("postgres:///x?host=h&port=")
err("postgres:///x?host=h&port=0")
err("redis://h/abc")
err("mysql:h/x")
err("sqlite://")
console.log("ok")
