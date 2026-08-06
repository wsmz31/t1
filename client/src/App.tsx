import { useEffect, useMemo, useState } from "react";

import { api, type Task } from "./api";
import "./App.css";

type Filter = "all" | "active" | "done";

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [title, setTitle] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .list()
      .then(setTasks)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const remaining = useMemo(
    () => tasks.filter((t) => !t.done).length,
    [tasks],
  );

  const visible = useMemo(() => {
    if (filter === "active") return tasks.filter((t) => !t.done);
    if (filter === "done") return tasks.filter((t) => t.done);
    return tasks;
  }, [tasks, filter]);

  async function addTask(e: React.FormEvent) {
    e.preventDefault();
    const value = title.trim();
    if (!value) return;
    try {
      const created = await api.create(value);
      setTasks((prev) => [...prev, created]);
      setTitle("");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function toggle(task: Task) {
    try {
      const updated = await api.update(task.id, { done: !task.done });
      setTasks((prev) => prev.map((t) => (t.id === task.id ? updated : t)));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(task: Task) {
    try {
      await api.remove(task.id);
      setTasks((prev) => prev.filter((t) => t.id !== task.id));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="app">
      <div className="card">
        <header className="header">
          <h1>Task Board</h1>
          <p className="subtitle">
            {remaining} {remaining === 1 ? "task" : "tasks"} remaining
          </p>
        </header>

        <form className="composer" onSubmit={addTask}>
          <input
            aria-label="New task"
            className="input"
            placeholder="What needs to be done?"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button className="btn" type="submit">
            Add
          </button>
        </form>

        <div className="filters" role="tablist">
          {(["all", "active", "done"] as Filter[]).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              className={`chip ${filter === f ? "chip--active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>

        {error && <div className="error">{error}</div>}

        {loading ? (
          <p className="empty">Loading…</p>
        ) : visible.length === 0 ? (
          <p className="empty">Nothing here yet.</p>
        ) : (
          <ul className="list">
            {visible.map((task) => (
              <li key={task.id} className={`item ${task.done ? "item--done" : ""}`}>
                <label className="item__main">
                  <input
                    type="checkbox"
                    checked={task.done}
                    onChange={() => toggle(task)}
                  />
                  <span className="item__title">{task.title}</span>
                </label>
                <button
                  className="item__delete"
                  aria-label={`Delete ${task.title}`}
                  onClick={() => remove(task)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <footer className="footnote">
        Full-stack demo · React + Vite frontend · Express API
      </footer>
    </div>
  );
}
