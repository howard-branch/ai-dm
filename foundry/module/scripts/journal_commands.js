// AI DM — journal entry helpers.
// Exposed to socket_bridge.js as create_journal / update_journal handlers.

export async function createJournal(title, content = "", folder = null) {
  if (!title || typeof title !== "string") {
    throw new Error("create_journal requires a non-empty title");
  }
  const data = {
    name: title,
    content: content || "",
  };
  if (folder) {
    data.folder = folder;
  }
  // Foundry v11+ JournalEntry shape: pages live as embedded docs.
  // Fall back to legacy `content` field if available; otherwise create
  // a single text page so the entry is non-empty.
  let entry;
  try {
    entry = await JournalEntry.create({
      name: data.name,
      folder: data.folder,
      pages: [
        {
          name: data.name,
          type: "text",
          text: { content: data.content, format: 1 },
        },
      ],
    });
  } catch (err) {
    // Older versions: { content: "..." }
    entry = await JournalEntry.create({ name: data.name, content: data.content, folder: data.folder });
  }
  return entry;
}

export async function updateJournal(journalId, { title = null, content = null } = {}) {
  if (!journalId) {
    throw new Error("update_journal requires journal_id");
  }
  const entry = game.journal.get(journalId);
  if (!entry) {
    throw new Error(`journal entry not found: ${journalId}`);
  }
  if (title !== null && title !== undefined) {
    await entry.update({ name: title });
  }
  if (content !== null && content !== undefined) {
    // Try v11 page-update path; fall back to legacy `content` field.
    const page = entry.pages?.contents?.[0];
    if (page && page.update) {
      const existing = page.text?.content || "";
      const merged = existing ? `${existing}\n\n${content}` : content;
      await page.update({ "text.content": merged });
    } else {
      const existing = entry.content || "";
      const merged = existing ? `${existing}\n\n${content}` : content;
      await entry.update({ content: merged });
    }
  }
  return entry;
}

