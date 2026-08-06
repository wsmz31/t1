import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(__dirname, "..", "data");
const DATA_FILE = join(DATA_DIR, "tasks.json");

const seedTasks = () => [
  {
    id: randomUUID(),
    title: "Set up the Cloud Agent environment",
    done: true,
    createdAt: new Date().toISOString(),
  },
  {
    id: randomUUID(),
    title: "Add your first task",
    done: false,
    createdAt: new Date().toISOString(),
  },
];

function load() {
  try {
    if (!existsSync(DATA_FILE)) {
      const seeded = seedTasks();
      persist(seeded);
      return seeded;
    }
    const raw = readFileSync(DATA_FILE, "utf8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function persist(tasks) {
  if (!existsSync(DATA_DIR)) {
    mkdirSync(DATA_DIR, { recursive: true });
  }
  writeFileSync(DATA_FILE, JSON.stringify(tasks, null, 2));
}

let tasks = load();

export function listTasks() {
  return [...tasks].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export function createTask(title) {
  const task = {
    id: randomUUID(),
    title: String(title).trim(),
    done: false,
    createdAt: new Date().toISOString(),
  };
  tasks.push(task);
  persist(tasks);
  return task;
}

export function updateTask(id, patch) {
  const task = tasks.find((t) => t.id === id);
  if (!task) return null;
  if (typeof patch.title === "string") task.title = patch.title.trim();
  if (typeof patch.done === "boolean") task.done = patch.done;
  persist(tasks);
  return task;
}

export function deleteTask(id) {
  const before = tasks.length;
  tasks = tasks.filter((t) => t.id !== id);
  const removed = tasks.length < before;
  if (removed) persist(tasks);
  return removed;
}
