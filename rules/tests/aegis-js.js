// Fixtures for rules/aegis-js.yml (semgrep --test). Not real code.
const cp = require("child_process");
const { exec, execFile } = require("child_process");
const jwt = require("jsonwebtoken");

// ---------------------------------------------------------------- aegis.js-child-process-exec-injection
function ping(host) {
  // ruleid: aegis.js-child-process-exec-injection
  cp.exec(`ping -c 1 ${host}`);
  // ruleid: aegis.js-child-process-exec-injection
  cp.execSync("ping -c 1 " + host);
  // ruleid: aegis.js-child-process-exec-injection
  exec("ping -c 1 " + host, (err) => {});
  // ruleid: aegis.js-child-process-exec-injection
  require("child_process").execSync(`rm -rf ${host}`);
  // ok: aegis.js-child-process-exec-injection
  cp.exec("ls -la");
  // ok: aegis.js-child-process-exec-injection
  execFile("ping", ["-c", "1", host]);
  // ok: aegis.js-child-process-exec-injection
  /ab+c/.exec("x" + host);
}

// ---------------------------------------------------------------- aegis.js-hardcoded-secret
// ruleid: aegis.js-hardcoded-secret
const STRIPE_API_KEY = "sk_live_51Habcdefabcdef";
// ruleid: aegis.js-hardcoded-secret
let dbPassword = "hunter2hunter2";
// ruleid: aegis.js-hardcoded-secret
var authToken = "abc123abc123";
const config = {
  // ruleid: aegis.js-hardcoded-secret
  clientSecret: "s3cr3t-value-123",
  // ok: aegis.js-hardcoded-secret
  host: "localhost",
};
// ok: aegis.js-hardcoded-secret
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
// ok: aegis.js-hardcoded-secret
const EMPTY_SECRET = "";

// ---------------------------------------------------------------- aegis.js-eval-injection
function calc(expr) {
  // ruleid: aegis.js-eval-injection
  eval(expr);
  // ok: aegis.js-eval-injection
  eval("1 + 2");
  // ok: aegis.js-eval-injection
  JSON.parse(expr);
}

// ---------------------------------------------------------------- aegis.js-new-function
function compile(body) {
  // ruleid: aegis.js-new-function
  const f = new Function("a", body);
  // ruleid: aegis.js-new-function
  const g = Function(body);
  // ok: aegis.js-new-function
  const h = new Function("a", "return a + 1");
  return [f, g, h];
}

// ---------------------------------------------------------------- aegis.js-reflected-xss
app.get("/hello", (req, res) => {
  // ruleid: aegis.js-reflected-xss
  res.send("<h1>Hello " + req.query.name + "</h1>");
});
app.get("/u/:id", function (req, res) {
  const id = req.params.id;
  // ruleid: aegis.js-reflected-xss
  res.write(`<p>${id}</p>`);
  res.end();
});
app.post("/echo", (req, res) => {
  // ok: aegis.js-reflected-xss
  res.send("<h1>Hello " + escapeHtml(req.body.name) + "</h1>");
});
app.get("/json", (req, res) => {
  // ok: aegis.js-reflected-xss
  res.json({ name: req.query.name });
  // ok: aegis.js-reflected-xss
  res.send("static");
});

// ---------------------------------------------------------------- aegis.js-jwt-none-algorithm
function auth(token, key) {
  // ruleid: aegis.js-jwt-none-algorithm
  jwt.verify(token, key, { algorithms: ["HS256", "none"] });
  // ruleid: aegis.js-jwt-none-algorithm
  jwt.verify(token, key, { algorithms: ['none'] }, (err, payload) => {});
  // ok: aegis.js-jwt-none-algorithm
  jwt.verify(token, key, { algorithms: ["HS256"] });
}

// ---------------------------------------------------------------- aegis.js-sql-string-concat
async function sql(knex, sequelize, db, id, name) {
  // ruleid: aegis.js-sql-string-concat
  await knex.raw("SELECT * FROM users WHERE id = " + id);
  // ruleid: aegis.js-sql-string-concat
  await sequelize.query(`SELECT * FROM users WHERE name = '${name}'`);
  // ruleid: aegis.js-sql-string-concat
  await db.query("DELETE FROM users WHERE id = " + id);
  // ruleid: aegis.js-sql-string-concat
  await knex("users").whereRaw("name = '" + name + "'");
  // ok: aegis.js-sql-string-concat
  await knex.raw("SELECT * FROM users WHERE id = ?", [id]);
  // ok: aegis.js-sql-string-concat
  await api.query("page=" + id);
  // ok: aegis.js-sql-string-concat
  await sequelize.query("SELECT * FROM users WHERE name = :name", { replacements: { name } });
  // ok: aegis.js-sql-string-concat
  await db.query("SELECT * FROM users WHERE id = $1", [id]);
}

// ---------------------------------------------------------------- aegis.js-nosql-where-injection
async function mongo(User, req) {
  // ruleid: aegis.js-nosql-where-injection
  await User.find({ $where: "this.name == '" + req.query.name + "'" });
  // ruleid: aegis.js-nosql-where-injection
  await User.find().$where(`this.age > ${req.query.age}`);
  // ok: aegis.js-nosql-where-injection
  await User.find({ $where: "this.age > 18" });
  // ok: aegis.js-nosql-where-injection
  await User.find({ name: String(req.query.name) });
}
