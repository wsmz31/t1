import cors from "cors";
import express from "express";

import {
  createTask,
  deleteTask,
  listTasks,
  updateTask,
} from "./store.js";

const app = express();
const PORT = Number(process.env.PORT) || 3001;

app.use(cors());
app.use(express.json());

app.get("/api/health", (_req, res) => {
  res.json({ status: "ok", uptime: process.uptime() });
});

app.get("/api/tasks", (_req, res) => {
  res.json(listTasks());
});

app.post("/api/tasks", (req, res) => {
  const title = req.body?.title;
  if (typeof title !== "string" || title.trim().length === 0) {
    return res.status(400).json({ error: "title is required" });
  }
  res.status(201).json(createTask(title));
});

app.patch("/api/tasks/:id", (req, res) => {
  const updated = updateTask(req.params.id, req.body ?? {});
  if (!updated) return res.status(404).json({ error: "task not found" });
  res.json(updated);
});

app.delete("/api/tasks/:id", (req, res) => {
  const removed = deleteTask(req.params.id);
  if (!removed) return res.status(404).json({ error: "task not found" });
  res.status(204).end();
});

app.listen(PORT, () => {
  console.log(`[server] Task API listening on http://localhost:${PORT}`);
});
