/* User-managed memories, shared by text and voice and retained in this browser. */
const LifelineMemories = (() => {
  "use strict";
  const KEY = "lifeline-memories";
  const LIMIT = 50;
  const MAX_LENGTH = 500;
  function list() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "[]");
      if (!Array.isArray(saved)) return [];
      const ids = new Set();
      return saved.filter((item) => {
        if (!item || typeof item.id !== "string" || ids.has(item.id) ||
            typeof item.text !== "string" || !item.text.trim() || item.text.length > MAX_LENGTH) return false;
        ids.add(item.id);
        return true;
      }).slice(0, LIMIT).map(({ id, text, at }) => ({ id, text: text.trim(), at }));
    } catch { return []; }
  }
  function write(items) {
    try { localStorage.setItem(KEY, JSON.stringify(items)); }
    catch { throw new Error("Could not save memories in this browser. Check your browser storage settings."); }
    document.dispatchEvent(new CustomEvent("memories-changed"));
  }
  function save(text, id) {
    text = String(text || "").trim();
    if (!text || text.length > MAX_LENGTH) throw new Error("Enter a memory of 1 to 500 characters.");
    const items = list();
    const duplicate = items.find((item) => item.id !== id && item.text.toLowerCase() === text.toLowerCase());
    if (duplicate) throw new Error("That memory is already saved.");
    if (id) {
      const item = items.find((item) => item.id === id);
      if (!item) throw new Error("That memory was removed. Save it as a new memory.");
      item.text = text;
    } else {
      if (items.length >= LIMIT) throw new Error("You can keep up to 50 memories. Delete one to add another.");
      items.push({ id: crypto.randomUUID(), text, at: new Date().toISOString() });
    }
    write(items);
  }
  function remove(id) { write(list().filter((item) => item.id !== id)); }
  function clear() { write([]); }
  window.addEventListener("storage", (event) => {
    if (event.key === KEY || event.key === null) document.dispatchEvent(new CustomEvent("memories-changed"));
  });
  return { list, save, remove, clear };
})();
