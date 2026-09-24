import { useMutation } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useApi, useUsers } from "../api/queries";

interface Props {
  userId: string | null;
  onSelect: (userId: string | null) => void;
}

/** Select or create the acting user; the selection is persisted by the parent (localStorage). */
export function UserPicker({ userId, onSelect }: Props) {
  const api = useApi();
  const users = useUsers();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const list = users.data;
  // Stored selection unknown to the server (e.g. DB reset) → fall back to the first user.
  useEffect(() => {
    if (!list) return;
    if (userId && list.some((u) => u.id === userId)) return;
    const fallback = list[0]?.id ?? null;
    if (fallback !== userId) onSelect(fallback);
  }, [list, userId, onSelect]);

  const create = useMutation({
    mutationFn: (userName: string) => api.createUser(userName),
    onSuccess: (user) => {
      setCreating(false);
      setName("");
      onSelect(user.id);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed) create.mutate(trimmed);
  };

  return (
    <div className="user-picker">
      <label className="section-label" htmlFor="user-select">
        User
      </label>
      <div className="user-picker-row">
        <select
          id="user-select"
          value={userId ?? ""}
          disabled={!list || list.length === 0}
          onChange={(e) => onSelect(e.target.value || null)}
        >
          {!list && <option value="">Loading…</option>}
          {list?.length === 0 && <option value="">No users yet</option>}
          {list?.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn btn-ghost"
          title="Create a user"
          onClick={() => setCreating((v) => !v)}
        >
          {creating ? "×" : "+"}
        </button>
      </div>
      {(creating || list?.length === 0) && (
        <form className="user-create" onSubmit={submit}>
          <input
            autoFocus
            placeholder="New user name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button type="submit" className="btn" disabled={!name.trim() || create.isPending}>
            Create
          </button>
        </form>
      )}
      {users.isError && <div className="error-text">Cannot load users: {users.error.message}</div>}
      {create.error && <div className="error-text">{create.error.message}</div>}
    </div>
  );
}
