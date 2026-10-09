// Tiny Express demo service used as AEGIS's monitored target. Intentionally minimal.
const express = require("express");
const { execFile } = require("child_process");

const app = express();
app.use(express.json());

const notes = [
  { id: 1, title: "welcome", body: "AEGIS is watching this repo." },
  { id: 2, title: "todo", body: "ship the demo" },
];

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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
  const name = escapeHtml(req.query.name || "world");
  res.type("html").send(`<h1>Hello ${name}</h1>`);
});

app.get("/ping", (req, res) => {
  const host = String(req.query.host || "127.0.0.1");
  if (!/^[a-zA-Z0-9.-]{1,253}$/.test(host)) return res.status(400).json({ error: "bad host" });
  execFile("ping", ["-c", "1", host], { timeout: 5000 }, (err, stdout) => {
    res.type("text").send(err ? "unreachable" : stdout);
  });
});

const PORT = Number(process.env.PORT || 3000);
app.listen(PORT, "127.0.0.1", () => console.log(`listening on ${PORT}`));
