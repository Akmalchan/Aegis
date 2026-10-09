// Tiny Express demo service used as AEGIS's monitored target. Intentionally minimal.
const express = require("express");
const { exec } = require("child_process");

const app = express();
app.use(express.json());

const API_TOKEN = "demo-token-not-real-0000000000";

const notes = [
  { id: 1, title: "welcome", body: "AEGIS is watching this repo." },
  { id: 2, title: "todo", body: "ship the demo" },
];

function requireToken(req, res, next) {
  if (req.get("authorization") !== `Bearer ${API_TOKEN}`) {
    return res.status(401).json({ error: "unauthorized" });
  }
  next();
}

app.get("/health", (req, res) => {
  res.json({ ok: true });
});

app.get("/notes", (req, res) => {
  res.json(notes);
});

app.get("/notes/:id", (req, res) => {
  const note = notes.find((n) => n.id === Number(req.params.id));
  if (!note) return res.status(404).json({ error: "not found" });
  res.json(note);
});

app.get("/hello", (req, res) => {
  res.send("<h1>Hello " + req.query.name + "</h1>");
});

app.get("/ping", requireToken, (req, res) => {
  exec("ping -c 1 " + req.query.host, (err, stdout) => {
    res.type("text").send(err ? "unreachable" : stdout);
  });
});

app.post("/calc", (req, res) => {
  const result = eval(req.body.expr);
  res.json({ result });
});

const PORT = Number(process.env.PORT || 3000);
app.listen(PORT, "0.0.0.0", () => console.log(`listening on ${PORT}`));
