/**
 * Character-creation wizard rendered inside the Foundry browser.
 *
 * Called when Python emits a ``wizard_request`` event because no live
 * character sheet exists for the active player. We render a single
 * Dialog with name / archetype / stat-bump / background fields and
 * post the answers back as a ``wizard_response`` event.
 *
 * The Python side rebuilds the full sheet from these four answers via
 * ``ai_dm.app.character_wizard.build_sheet`` — we don't compute stats
 * here; we just collect choices.
 */

let activeDialog = null;

// ------------------------------------------------------------------ //
// Client setting: choose between the v1 single long form and the v2
// paged DialogV2 sequence. Registered lazily on first use so we don't
// have to coordinate module init ordering.
// ------------------------------------------------------------------ //

const SETTING_NS = "ai-dm-bridge";
const SETTING_PAGED = "wizard.paged";
let _settingRegistered = false;

function _ensurePagedSettingRegistered() {
  if (_settingRegistered) return;
  try {
    game.settings.register(SETTING_NS, SETTING_PAGED, {
      name: "Paged character wizard",
      hint: "Show the character-creation wizard as a sequence of small pages instead of one long form.",
      scope: "client",
      config: true,
      type: Boolean,
      default: false,
    });
    _settingRegistered = true;
  } catch (err) {
    // Some Foundry versions throw if registered twice; treat as success.
    _settingRegistered = true;
  }
}

function _isPagedEnabled() {
  _ensurePagedSettingRegistered();
  try {
    return Boolean(game.settings.get(SETTING_NS, SETTING_PAGED));
  } catch {
    return false;
  }
}

/**
 * Constrain the DialogV2 window to the browser viewport so its content
 * scrolls instead of overflowing off-screen. DialogV2 sizes itself to
 * its content by default; without this, a tall form (lots of
 * archetypes / shop items / spells) pushes the OK / Cancel buttons
 * below the fold and the user can't reach them.
 *
 * Applied in the ``render`` callback of every wizard dialog. Walks up
 * from the form root to the Foundry window-app container and pins
 * ``max-height`` + ``overflow-y: auto`` on the ``.window-content``
 * scroll region.
 */
function constrainDialogToViewport(root) {
  if (!root) return;
  const win = root.closest?.(".window-app") || root.closest?.(".application");
  if (!win) return;
  // Cap the window itself to ~92% of the viewport. The header + button
  // row live outside .window-content, so we leave a little slack.
  win.style.maxHeight = "92vh";
  const content = win.querySelector(".window-content");
  if (content) {
    content.style.maxHeight = "calc(92vh - 6rem)";
    content.style.overflowY = "auto";
    content.style.overflowX = "hidden";
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderForm(payload) {
  const archetypes = payload.archetypes || [];
  const backgrounds = payload.backgrounds || [];
  const abilities = payload.abilities || ["str", "dex", "con", "int", "wis", "cha"];
  const itemsCatalog = payload.items || {};
  const spellCatalog = payload.spells || {};
  const kits = payload.kits || {};
  const errors = Array.isArray(payload.errors) ? payload.errors : [];
  const prev = payload.previous_answers || {};

  const errorBlock = errors.length
    ? `<div class="ai-dm-wizard-errors" role="alert"
            style="background:#fee;border:1px solid #c33;color:#900;
                   padding:0.5rem 0.75rem;border-radius:4px">
         <strong>Please fix the following:</strong>
         <ul style="margin:0.25rem 0 0.5rem 1rem;padding:0">
           ${errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}
         </ul>
       </div>`
    : "";

  const prevName = escapeHtml(prev.name || "");
  const prevArchetype = String(prev.archetype || "").toLowerCase();
  const prevBackground = String(prev.background || "").toLowerCase();
  const prevStatBump = String(prev.stat_bump || "").toLowerCase();
  const prevPurchases = new Set((prev.purchases || []).map((s) => String(s)));
  const prevCantrips = new Set((prev.cantrip_picks || []).map((s) => String(s)));
  const prevSpells = new Set((prev.spell_picks || []).map((s) => String(s)));

  const archetypeOptions = archetypes
    .map((a, i) => {
      const isPrev = prevArchetype && a.key.toLowerCase() === prevArchetype;
      const checked = isPrev || (!prevArchetype && i === 0) ? "checked" : "";
      const stats = a.abilities
        ? Object.entries(a.abilities)
            .map(([k, v]) => `${k.toUpperCase()} ${v}`)
            .join(" · ")
        : "";
      const sc = a.spellcasting
        ? ` · spellcaster (${a.spellcasting.cantrips_known || 0} cantrips, ${a.spellcasting.spells_known || 0} L1)`
        : "";
      return `
        <label class="ai-dm-wizard-option">
          <input type="radio" name="archetype" value="${escapeHtml(a.key)}" ${checked} data-archetype />
          <strong>${escapeHtml(a.emoji || "")} ${escapeHtml(a.label)}</strong>
          <div class="ai-dm-wizard-blurb">${escapeHtml(a.blurb || "")}</div>
          ${stats ? `<div class="ai-dm-wizard-stats">${escapeHtml(stats + sc)}</div>` : ""}
        </label>`;
    })
    .join("");

  const backgroundOptions = backgrounds
    .map((b, i) => {
      const isPrev = prevBackground && b.key.toLowerCase() === prevBackground;
      const checked = isPrev || (!prevBackground && i === 0) ? "checked" : "";
      return `
        <label class="ai-dm-wizard-option">
          <input type="radio" name="background" value="${escapeHtml(b.key)}" ${checked} />
          <strong>${escapeHtml(b.label)}</strong>
          <div class="ai-dm-wizard-blurb">${escapeHtml(b.blurb || "")}</div>
        </label>`;
    })
    .join("");

  const statOptions = ["", ...abilities]
    .map((k) => {
      const label = k ? k.toUpperCase() : "— (no bump)";
      const sel = k.toLowerCase() === prevStatBump ? "selected" : "";
      return `<option value="${escapeHtml(k)}" ${sel}>${escapeHtml(label)}</option>`;
    })
    .join("");

  // Per-archetype kit/shopping/spell sections, all rendered up-front
  // and shown/hidden via JS when the player flips the archetype radio.
  // Build one block per archetype and toggle visibility client-side.
  const kitBlocks = archetypes
    .map((a) => {
      const kit = kits[a.key] || {};
      const items = (kit.items || [])
        .map((it) => {
          const cat = itemsCatalog[it.id] || {};
          const tag = it.equipped ? " (equipped)" : "";
          const qty = it.qty && it.qty !== 1 ? ` x${it.qty}` : "";
          return `<li>${escapeHtml(cat.name || it.id)}${qty}${tag}</li>`;
        })
        .join("");
      const startGp = (kit.currency || {}).gp || 0;
      const budget = kit.shopping_budget_gp || 0;
      const shopRows = budget > 0
        ? Object.entries(itemsCatalog)
            .filter(([, rec]) => Number(rec.value_gp || 0) <= budget)
            .sort((a1, a2) => String(a1[1].name || a1[0]).localeCompare(a2[1].name || a2[0]))
            .map(([iid, rec]) => {
              const isPrev = prevPurchases.has(iid);
              return `
                <label class="ai-dm-wizard-shop-row">
                  <input type="checkbox" name="purchase__${escapeHtml(a.key)}"
                         value="${escapeHtml(iid)}"
                         data-cost="${Number(rec.value_gp || 0)}"
                         ${isPrev ? "checked" : ""} />
                  ${escapeHtml(rec.name || iid)} —
                  <span style="opacity:0.7">${Number(rec.value_gp || 0)} gp</span>
                </label>`;
            })
            .join("")
        : "";
      return `
        <div class="ai-dm-wizard-kit" data-kit="${escapeHtml(a.key)}" style="display:none">
          <strong>Starting kit:</strong>
          <ul style="margin:0.25rem 0 0.5rem 1rem;padding:0">${items}</ul>
          <div>Starting gold: <strong>${startGp} gp</strong></div>
          ${budget > 0 ? `
            <details style="margin-top:0.5rem">
              <summary>Spend up to <strong>${budget} gp</strong> on extra gear
                (<span data-budget-remaining="${escapeHtml(a.key)}">${budget}</span> gp left)
              </summary>
              <div class="ai-dm-wizard-shop" data-shop="${escapeHtml(a.key)}" data-budget="${budget}">
                ${shopRows}
              </div>
            </details>` : ""}
        </div>`;
    })
    .join("");

  // Per-archetype spell pickers (only rendered for casters).
  const spellBlocks = archetypes
    .filter((a) => a.spellcasting)
    .map((a) => {
      const sc = a.spellcasting;
      const cantripCap = Number(sc.cantrips_known || 0);
      const spellCap = Number(sc.spells_known || 0);

      const renderList = (level, cap, fieldName, prevSet) => {
        if (cap <= 0) return "";
        const pool = Object.entries(spellCatalog)
          .filter(([, rec]) =>
            Number(rec.level) === level &&
            (!rec.archetypes || rec.archetypes.includes(a.key))
          )
          .sort((a1, a2) => String(a1[1].name || a1[0]).localeCompare(a2[1].name || a2[0]));
        const rows = pool
          .map(([sid, rec]) => `
            <label class="ai-dm-wizard-spell-row">
              <input type="checkbox" name="${escapeHtml(fieldName)}__${escapeHtml(a.key)}"
                     value="${escapeHtml(sid)}"
                     data-cap="${cap}"
                     ${prevSet.has(sid) ? "checked" : ""} />
              <strong>${escapeHtml(rec.name || sid)}</strong>
              <span style="opacity:0.7">— ${escapeHtml((rec.description || "").slice(0, 80))}</span>
            </label>`)
          .join("");
        const label = level === 0 ? "cantrips" : `level-${level} spells`;
        return `
          <div style="margin-top:0.5rem">
            <strong>Pick up to ${cap} ${label}</strong>
            <div class="ai-dm-wizard-spells">${rows}</div>
          </div>`;
      };

      return `
        <div class="ai-dm-wizard-spellbook" data-spellbook="${escapeHtml(a.key)}" style="display:none">
          ${renderList(0, cantripCap, "cantrip", prevCantrips)}
          ${renderList(1, spellCap, "spell", prevSpells)}
        </div>`;
    })
    .join("");

  return `
    <form class="ai-dm-wizard">
      <style>
        .ai-dm-wizard {
          display: flex; flex-direction: column; gap: 0.75rem;
        }
        .ai-dm-wizard fieldset { border: 1px solid #888; padding: 0.5rem; }
        .ai-dm-wizard legend { font-weight: bold; }
        .ai-dm-wizard-option { display: block; padding: 0.25rem 0; cursor: pointer; }
        .ai-dm-wizard-blurb { font-size: 0.85em; opacity: 0.85; margin-left: 1.25rem; }
        .ai-dm-wizard-stats { font-size: 0.8em; opacity: 0.7; margin-left: 1.25rem; }
        .ai-dm-wizard input[type="text"] { width: 100%; }
        .ai-dm-wizard-shop-row, .ai-dm-wizard-spell-row { display: block; padding: 0.15rem 0; cursor: pointer; }
        /* Bound the very long shop / spell lists so a single section
           can't make the dialog absurdly tall before the outer
           window-content scroller kicks in. */
        .ai-dm-wizard-shop, .ai-dm-wizard-spells {
          max-height: 40vh; overflow-y: auto; padding-right: 0.25rem;
        }
      </style>

      ${errorBlock}

      <label>
        <strong>Character name</strong>
        <input type="text" name="name" required autofocus value="${prevName}" />
      </label>

      <fieldset>
        <legend>Archetype</legend>
        ${archetypeOptions}
      </fieldset>

      <label>
        <strong>Bump one ability by +1</strong>
        <select name="stat_bump">${statOptions}</select>
      </label>

      <fieldset>
        <legend>Background</legend>
        ${backgroundOptions}
      </fieldset>

      <fieldset>
        <legend>Equipment</legend>
        ${kitBlocks}
      </fieldset>

      ${spellBlocks ? `
      <fieldset>
        <legend>Spells</legend>
        ${spellBlocks}
      </fieldset>` : ""}
    </form>
  `;
}

/**
 * Wire up the dynamic bits of the form:
 *   • show the kit + spell blocks for the currently-selected archetype,
 *   • enforce the shopping budget by disabling unaffordable checkboxes,
 *   • enforce the spell caps by disabling extra checkboxes.
 */
function wireFormDynamics(root) {
  if (!root) return;
  const showForArchetype = (key) => {
    root.querySelectorAll("[data-kit]").forEach((el) => {
      el.style.display = el.dataset.kit === key ? "" : "none";
    });
    root.querySelectorAll("[data-spellbook]").forEach((el) => {
      el.style.display = el.dataset.spellbook === key ? "" : "none";
    });
  };
  const recalcBudget = () => {
    root.querySelectorAll("[data-shop]").forEach((shop) => {
      const key = shop.dataset.shop;
      const budget = Number(shop.dataset.budget || 0);
      let spent = 0;
      shop.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        if (cb.checked) spent += Number(cb.dataset.cost || 0);
      });
      const remaining = budget - spent;
      const span = root.querySelector(`[data-budget-remaining="${key}"]`);
      if (span) span.textContent = String(remaining);
      shop.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        if (cb.checked) return;
        cb.disabled = Number(cb.dataset.cost || 0) > remaining;
      });
    });
  };
  const recalcSpellCaps = () => {
    // Group checkboxes by name (each name contains the cap-per-archetype).
    const groups = new Map();
    root.querySelectorAll(".ai-dm-wizard-spells input[type=checkbox]").forEach((cb) => {
      const arr = groups.get(cb.name) || [];
      arr.push(cb);
      groups.set(cb.name, arr);
    });
    groups.forEach((arr) => {
      const cap = Number(arr[0]?.dataset.cap || 0);
      const checked = arr.filter((cb) => cb.checked).length;
      arr.forEach((cb) => {
        if (cb.checked) return;
        cb.disabled = checked >= cap;
      });
    });
  };
  root.querySelectorAll("input[name=archetype]").forEach((radio) => {
    radio.addEventListener("change", () => {
      showForArchetype(radio.value);
      recalcBudget();
      recalcSpellCaps();
    });
  });
  root.querySelectorAll(".ai-dm-wizard-shop input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", recalcBudget);
  });
  root.querySelectorAll(".ai-dm-wizard-spells input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", recalcSpellCaps);
  });
  // Initial paint based on the currently-checked archetype.
  const checked = root.querySelector("input[name=archetype]:checked");
  if (checked) showForArchetype(checked.value);
  recalcBudget();
  recalcSpellCaps();
}

function readFormFromDialog(dialog) {
  // DialogV2 callbacks receive (event, button, dialog). The dialog's
  // root element is .element; older builds may pass an HTMLElement or
  // a jQuery-wrapped node. Normalise to a real element.
  const root =
    dialog?.element instanceof HTMLElement ? dialog.element :
    dialog instanceof HTMLElement ? dialog :
    dialog?.[0] ?? null;
  if (!root) return null;
  const form = root.querySelector("form") ?? root;
  const fd = new FormData(form);
  const name = String(fd.get("name") || "").trim();
  const archetype = String(fd.get("archetype") || "").trim();
  const background = String(fd.get("background") || "").trim();
  const stat_bump = String(fd.get("stat_bump") || "").trim() || null;
  if (!name || !archetype || !background) return null;
  // Per-archetype selections — only the active archetype's checkboxes
  // contribute (the others are display:none but still in the DOM).
  const purchases = fd.getAll(`purchase__${archetype}`).map(String).filter(Boolean);
  const cantrip_picks = fd.getAll(`cantrip__${archetype}`).map(String).filter(Boolean);
  const spell_picks = fd.getAll(`spell__${archetype}`).map(String).filter(Boolean);
  return { name, archetype, stat_bump, background, purchases, cantrip_picks, spell_picks };
}

export async function openCharacterWizard(payload, sendEventToPython) {
  // The GM doesn't play a character — silently ignore the prompt on
  // GM clients. Python will keep re-emitting wizard_request every few
  // seconds, so the dialog will pop up the moment a player connects.
  if (game?.user?.isGM) {
    console.log("AI DM Bridge: wizard_request ignored on GM client");
    return;
  }

  // De-dupe: Python re-emits the request periodically until a response
  // arrives, so just keep the existing dialog open if one is showing.
  if (activeDialog) return;

  const pcId = payload?.pc_id || "player";
  const userInfo = {
    user_id: game?.user?.id ?? null,
    user_name: game?.user?.name ?? null,
  };

  const DialogV2 = foundry?.applications?.api?.DialogV2;
  if (!DialogV2) {
    console.warn("AI DM Bridge: DialogV2 unavailable on this Foundry version");
    return;
  }

  // Branch: paged sequence (opt-in setting) vs. v1 single long form.
  if (_isPagedEnabled()) {
    activeDialog = true;
    try {
      await runPagedWizard(payload || {}, pcId, userInfo, sendEventToPython, DialogV2);
    } finally {
      activeDialog = null;
    }
    return;
  }

  const content = renderForm(payload || {});

  activeDialog = true;
  try {
    let answers = null;
    let cancelled = false;

    await DialogV2.wait({
      window: { title: "Create your character" },
      position: { width: 620 },
      content,
      rejectClose: false,
      render: (event, dialog) => {
        // DialogV2 fires "render" once the DOM is built. Wire up the
        // archetype-driven show/hide + budget/cap enforcement here.
        const root =
          dialog?.element instanceof HTMLElement ? dialog.element :
          event?.target instanceof HTMLElement ? event.target : null;
        try {
          constrainDialogToViewport(root);
          wireFormDynamics(root);
        } catch (err) {
          console.warn("AI DM Bridge: wizard dynamics wiring failed", err);
        }
      },
      buttons: [
        {
          action: "cancel",
          label: "Skip",
          icon: "fa-solid fa-xmark",
          callback: () => {
            cancelled = true;
            return "cancel";
          },
        },
        {
          action: "confirm",
          label: "Begin adventure",
          icon: "fa-solid fa-check",
          default: true,
          // Returning false from a button callback in DialogV2 keeps
          // the dialog open. We use that to nag the user when fields
          // are blank instead of submitting an empty payload.
          callback: (event, button, dialog) => {
            const result = readFormFromDialog(dialog);
            if (!result) {
              ui.notifications?.warn("Please fill in every field.");
              return false;
            }
            answers = result;
            return "confirm";
          },
        },
      ],
    });

    if (cancelled) {
      console.log("AI DM Bridge: wizard explicitly skipped — sending cancelled response");
      sendEventToPython("wizard_response", {
        pc_id: pcId,
        cancelled: true,
        ...userInfo,
      });
      return;
    }
    if (!answers) {
      // Dialog dismissed via the window's close button / Escape — the
      // user didn't press Skip, so don't tell Python they cancelled.
      // Just bail; Python re-emits wizard_request periodically and the
      // dialog will pop right back up (activeDialog is cleared in
      // the finally block below).
      console.log("AI DM Bridge: wizard dialog closed without an answer — awaiting next wizard_request");
      ui.notifications?.info("Character creation is required to start. The dialog will reopen shortly.");
      return;
    }
    console.log("AI DM Bridge: wizard submitted, sending wizard_response", {
      pc_id: pcId,
      user_id: userInfo.user_id,
      user_name: userInfo.user_name,
      ...answers,
    });
    sendEventToPython("wizard_response", {
      pc_id: pcId,
      ...userInfo,
      ...answers,
    });
  } finally {
    activeDialog = null;
  }
}


// ====================================================================== //
// Paged DialogV2 wizard (v2)
//
// Same wire format as the v1 single-form wizard — emits a single
// ``wizard_response`` event with the same fields — but presented as a
// six-or-seven-step DialogV2 sequence with Back/Next/Skip controls.
// Each page is a separate ``DialogV2.wait`` call; state is held in a
// plain object that's threaded forward (and backward, on Back).
//
// Pages:
//   1. identity    — name
//   2. archetype
//   3. stats       — optional +1 bump (preview reflects the choice)
//   4. background
//   5. equipment   — kit preview + optional shopping (skipped when
//                    the chosen archetype has no shopping budget)
//   6. spells      — cantrips + level-1 picks (skipped for non-casters)
//   7. summary     — read-only review with Begin / Back / Skip
// ====================================================================== //

function _abilities(payload) {
  return payload.abilities || ["str", "dex", "con", "int", "wis", "cha"];
}

function _archetypeByKey(payload, key) {
  return (payload.archetypes || []).find((a) => a.key === key) || null;
}

function _backgroundByKey(payload, key) {
  return (payload.backgrounds || []).find((b) => b.key === key) || null;
}

function _archetypeNeedsShopping(payload, key) {
  const kit = (payload.kits || {})[key] || {};
  return Number(kit.shopping_budget_gp || 0) > 0;
}

function _archetypeIsCaster(payload, key) {
  const arch = _archetypeByKey(payload, key);
  return !!(arch && arch.spellcasting);
}

// ---- per-page renderers ---------------------------------------------- //

function _commonStyles() {
  return `
    <style>
      .ai-dm-wizard-page {
        display: flex; flex-direction: column; gap: 0.75rem;
      }
      .ai-dm-wizard-page fieldset { border: 1px solid #888; padding: 0.5rem; }
      .ai-dm-wizard-page legend { font-weight: bold; }
      .ai-dm-wizard-page label.opt { display: block; padding: 0.25rem 0; cursor: pointer; }
      .ai-dm-wizard-page .blurb { font-size: 0.85em; opacity: 0.85; margin-left: 1.25rem; }
      .ai-dm-wizard-page .stats { font-size: 0.8em; opacity: 0.7; margin-left: 1.25rem; }
      .ai-dm-wizard-page input[type="text"] { width: 100%; }
      .ai-dm-wizard-page .row { display: block; padding: 0.15rem 0; cursor: pointer; }
      .ai-dm-wizard-page .ai-dm-wizard-shop,
      .ai-dm-wizard-page .ai-dm-wizard-spells {
        max-height: 40vh; overflow-y: auto; padding-right: 0.25rem;
      }
      .ai-dm-wizard-step { font-size: 0.85em; opacity: 0.7; margin-bottom: 0.25rem; }
    </style>`;
}

function _stepHeader(state, label) {
  const total = state._totalSteps || 7;
  const idx = state._stepIndex || 1;
  return `<div class="ai-dm-wizard-step">Step ${idx} of ${total} — ${escapeHtml(label)}</div>`;
}

function _renderIdentity(payload, state) {
  const prev = escapeHtml(state.name || "");
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Identity")}
      <label>
        <strong>Character name</strong>
        <input type="text" name="name" required autofocus value="${prev}" />
      </label>
    </form>`;
}

function _renderArchetype(payload, state) {
  const archetypes = payload.archetypes || [];
  const sel = String(state.archetype || "").toLowerCase();
  const opts = archetypes.map((a, i) => {
    const checked = (sel ? a.key.toLowerCase() === sel : i === 0) ? "checked" : "";
    const stats = a.abilities
      ? Object.entries(a.abilities).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(" · ")
      : "";
    const sc = a.spellcasting
      ? ` · spellcaster (${a.spellcasting.cantrips_known || 0} cantrips, ${a.spellcasting.spells_known || 0} L1)`
      : "";
    return `
      <label class="opt">
        <input type="radio" name="archetype" value="${escapeHtml(a.key)}" ${checked} />
        <strong>${escapeHtml(a.emoji || "")} ${escapeHtml(a.label)}</strong>
        <div class="blurb">${escapeHtml(a.blurb || "")}</div>
        ${stats ? `<div class="stats">${escapeHtml(stats + sc)}</div>` : ""}
      </label>`;
  }).join("");
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Archetype")}
      <fieldset><legend>Choose an archetype</legend>${opts}</fieldset>
    </form>`;
}

function _renderStats(payload, state) {
  const arch = _archetypeByKey(payload, state.archetype);
  const abilities = _abilities(payload);
  const bump = String(state.stat_bump || "").toLowerCase();
  const baseRows = arch && arch.abilities
    ? abilities.map((k) => {
        const base = Number(arch.abilities[k] || 10);
        const adj = bump === k ? base + 1 : base;
        const tag = bump === k ? " <em>(+1)</em>" : "";
        return `<li>${k.toUpperCase()}: <strong>${adj}</strong>${tag}</li>`;
      }).join("")
    : "";
  const opts = ["", ...abilities].map((k) => {
    const label = k ? k.toUpperCase() : "— (no bump)";
    const sel = k.toLowerCase() === bump ? "selected" : "";
    return `<option value="${escapeHtml(k)}" ${sel}>${escapeHtml(label)}</option>`;
  }).join("");
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Stats")}
      <div><strong>Starting stats${arch ? ` for ${escapeHtml(arch.label)}` : ""}:</strong>
        <ul style="margin:0.25rem 0 0.5rem 1rem;padding:0">${baseRows}</ul>
      </div>
      <label>
        <strong>Bump one ability by +1</strong>
        <select name="stat_bump">${opts}</select>
      </label>
    </form>`;
}

function _renderBackground(payload, state) {
  const backgrounds = payload.backgrounds || [];
  const sel = String(state.background || "").toLowerCase();
  const opts = backgrounds.map((b, i) => {
    const checked = (sel ? b.key.toLowerCase() === sel : i === 0) ? "checked" : "";
    return `
      <label class="opt">
        <input type="radio" name="background" value="${escapeHtml(b.key)}" ${checked} />
        <strong>${escapeHtml(b.label)}</strong>
        <div class="blurb">${escapeHtml(b.blurb || "")}</div>
      </label>`;
  }).join("");
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Background")}
      <fieldset><legend>Choose a background</legend>${opts}</fieldset>
    </form>`;
}

function _renderEquipment(payload, state) {
  const arch = _archetypeByKey(payload, state.archetype);
  const kit = (payload.kits || {})[state.archetype] || {};
  const items = (kit.items || []).map((it) => {
    const cat = (payload.items || {})[it.id] || {};
    const tag = it.equipped ? " (equipped)" : "";
    const qty = it.qty && it.qty !== 1 ? ` x${it.qty}` : "";
    return `<li>${escapeHtml(cat.name || it.id)}${qty}${tag}</li>`;
  }).join("");
  const startGp = (kit.currency || {}).gp || 0;
  const budget = Number(kit.shopping_budget_gp || 0);
  const prevPurchases = new Set((state.purchases || []).map(String));
  const shopRows = budget > 0
    ? Object.entries(payload.items || {})
        .filter(([, rec]) => Number(rec.value_gp || 0) <= budget)
        .sort((a, b) => String(a[1].name || a[0]).localeCompare(b[1].name || b[0]))
        .map(([iid, rec]) => `
          <label class="row">
            <input type="checkbox" name="purchase" value="${escapeHtml(iid)}"
                   data-cost="${Number(rec.value_gp || 0)}"
                   ${prevPurchases.has(iid) ? "checked" : ""} />
            ${escapeHtml(rec.name || iid)} —
            <span style="opacity:0.7">${Number(rec.value_gp || 0)} gp</span>
          </label>`).join("")
    : "";
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Equipment")}
      <div><strong>Starting kit${arch ? ` (${escapeHtml(arch.label)})` : ""}:</strong>
        <ul style="margin:0.25rem 0 0.5rem 1rem;padding:0">${items}</ul>
        <div>Starting gold: <strong>${startGp} gp</strong></div>
      </div>
      ${budget > 0 ? `
        <fieldset>
          <legend>Spend up to <strong>${budget} gp</strong>
            (<span data-budget-remaining>${budget}</span> gp left)</legend>
          <div class="ai-dm-wizard-shop" data-budget="${budget}">${shopRows}</div>
        </fieldset>` : `<div><em>No shopping budget for this archetype.</em></div>`}
    </form>`;
}

function _renderSpellPicker(payload, state, level, cap, fieldName) {
  if (cap <= 0) return "";
  const prevSet = new Set(((fieldName === "cantrip" ? state.cantrip_picks : state.spell_picks) || []).map(String));
  const pool = Object.entries(payload.spells || {})
    .filter(([, rec]) =>
      Number(rec.level) === level &&
      (!rec.archetypes || rec.archetypes.includes(state.archetype))
    )
    .sort((a, b) => String(a[1].name || a[0]).localeCompare(b[1].name || b[0]));
  const rows = pool.map(([sid, rec]) => `
    <label class="row">
      <input type="checkbox" name="${escapeHtml(fieldName)}" value="${escapeHtml(sid)}"
             data-cap="${cap}" ${prevSet.has(sid) ? "checked" : ""} />
      <strong>${escapeHtml(rec.name || sid)}</strong>
      <span style="opacity:0.7">— ${escapeHtml((rec.description || "").slice(0, 80))}</span>
    </label>`).join("");
  const label = level === 0 ? "cantrips" : `level-${level} spells`;
  return `
    <fieldset>
      <legend>Pick up to ${cap} ${label}</legend>
      <div class="ai-dm-wizard-spells">${rows}</div>
    </fieldset>`;
}

function _renderSpells(payload, state) {
  const arch = _archetypeByKey(payload, state.archetype);
  const sc = arch ? arch.spellcasting : null;
  if (!sc) return `${_commonStyles()}<form class="ai-dm-wizard-page">${_stepHeader(state, "Spells")}<div><em>This archetype is not a spellcaster.</em></div></form>`;
  const cantripCap = Number(sc.cantrips_known || 0);
  const spellCap = Number(sc.spells_known || 0);
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Spells")}
      ${_renderSpellPicker(payload, state, 0, cantripCap, "cantrip")}
      ${_renderSpellPicker(payload, state, 1, spellCap, "spell")}
    </form>`;
}

function _renderSummary(payload, state) {
  const arch = _archetypeByKey(payload, state.archetype);
  const bg = _backgroundByKey(payload, state.background);
  const bump = state.stat_bump ? state.stat_bump.toUpperCase() : "—";
  const purchaseLines = (state.purchases || []).map((iid) => {
    const rec = (payload.items || {})[iid] || {};
    return `<li>${escapeHtml(rec.name || iid)}</li>`;
  }).join("") || "<li><em>none</em></li>";
  const cantripLines = (state.cantrip_picks || []).map((sid) => {
    const rec = (payload.spells || {})[sid] || {};
    return `<li>${escapeHtml(rec.name || sid)}</li>`;
  }).join("") || "<li><em>none</em></li>";
  const spellLines = (state.spell_picks || []).map((sid) => {
    const rec = (payload.spells || {})[sid] || {};
    return `<li>${escapeHtml(rec.name || sid)}</li>`;
  }).join("") || "<li><em>none</em></li>";
  return `
    ${_commonStyles()}
    <form class="ai-dm-wizard-page">
      ${_stepHeader(state, "Review")}
      <div><strong>Name:</strong> ${escapeHtml(state.name || "")}</div>
      <div><strong>Archetype:</strong> ${escapeHtml(arch ? arch.label : state.archetype || "")}</div>
      <div><strong>Background:</strong> ${escapeHtml(bg ? bg.label : state.background || "")}</div>
      <div><strong>+1 bump:</strong> ${escapeHtml(bump)}</div>
      <div><strong>Purchases:</strong><ul style="margin:0.25rem 0 0 1rem">${purchaseLines}</ul></div>
      ${arch && arch.spellcasting ? `
        <div><strong>Cantrips:</strong><ul style="margin:0.25rem 0 0 1rem">${cantripLines}</ul></div>
        <div><strong>Spells:</strong><ul style="margin:0.25rem 0 0 1rem">${spellLines}</ul></div>` : ""}
    </form>`;
}

// ---- per-page validators (return null OR error string) --------------- //

function _readIdentity(form) {
  const name = String(new FormData(form).get("name") || "").trim();
  if (name.length < 2) return { error: "Please enter a name (2+ characters)." };
  return { patch: { name } };
}
function _readArchetype(form) {
  const v = String(new FormData(form).get("archetype") || "").trim();
  if (!v) return { error: "Please pick an archetype." };
  return { patch: { archetype: v } };
}
function _readStats(form) {
  const v = String(new FormData(form).get("stat_bump") || "").trim() || null;
  return { patch: { stat_bump: v } };
}
function _readBackground(form) {
  const v = String(new FormData(form).get("background") || "").trim();
  if (!v) return { error: "Please pick a background." };
  return { patch: { background: v } };
}
function _readEquipment(form) {
  const purchases = new FormData(form).getAll("purchase").map(String).filter(Boolean);
  return { patch: { purchases } };
}
function _readSpells(form) {
  const fd = new FormData(form);
  const cantrip_picks = fd.getAll("cantrip").map(String).filter(Boolean);
  const spell_picks = fd.getAll("spell").map(String).filter(Boolean);
  return { patch: { cantrip_picks, spell_picks } };
}

// ---- per-page wiring (budget + spell-cap enforcement) ---------------- //

function _wireEquipment(root) {
  const shop = root.querySelector(".ai-dm-wizard-shop");
  if (!shop) return;
  const budget = Number(shop.dataset.budget || 0);
  const remainingSpan = root.querySelector("[data-budget-remaining]");
  const recalc = () => {
    let spent = 0;
    shop.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      if (cb.checked) spent += Number(cb.dataset.cost || 0);
    });
    const remaining = budget - spent;
    if (remainingSpan) remainingSpan.textContent = String(remaining);
    shop.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      if (cb.checked) return;
      cb.disabled = Number(cb.dataset.cost || 0) > remaining;
    });
  };
  shop.querySelectorAll("input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", recalc);
  });
  recalc();
}

function _wireSpells(root) {
  const groups = new Map();
  root.querySelectorAll(".ai-dm-wizard-spells input[type=checkbox]").forEach((cb) => {
    const arr = groups.get(cb.name) || [];
    arr.push(cb);
    groups.set(cb.name, arr);
  });
  const recalc = () => {
    groups.forEach((arr) => {
      const cap = Number(arr[0]?.dataset.cap || 0);
      const checked = arr.filter((cb) => cb.checked).length;
      arr.forEach((cb) => {
        if (cb.checked) return;
        cb.disabled = checked >= cap;
      });
    });
  };
  groups.forEach((arr) => {
    arr.forEach((cb) => cb.addEventListener("change", recalc));
  });
  recalc();
}

// ---- driver ----------------------------------------------------------- //

/**
 * Run a single page. Returns ``{action, patch?}`` where action is one
 * of ``"next"``, ``"back"``, ``"cancel"``, or ``"submit"``. ``patch``
 * is the page's contribution to the running ``state``.
 */
async function _runPage(DialogV2, payload, state, page) {
  const content = page.render(payload, state);
  // Distinguish window-close (Esc / X) from the explicit Skip button:
  // the former leaves ``result`` at "dismissed" so the caller can
  // bail-without-cancel, matching the v1 single-form behavior.
  let result = { action: "dismissed" };
  const buttons = [];
  if (page.canBack) {
    buttons.push({
      action: "back",
      label: "Back",
      icon: "fa-solid fa-arrow-left",
      callback: () => { result = { action: "back" }; return "back"; },
    });
  }
  buttons.push({
    action: "cancel",
    label: "Skip",
    icon: "fa-solid fa-xmark",
    callback: () => { result = { action: "cancel" }; return "cancel"; },
  });
  buttons.push({
    action: "next",
    label: page.isLast ? "Begin adventure" : "Next",
    icon: page.isLast ? "fa-solid fa-check" : "fa-solid fa-arrow-right",
    default: true,
    callback: (event, button, dialog) => {
      const root =
        dialog?.element instanceof HTMLElement ? dialog.element :
        dialog instanceof HTMLElement ? dialog :
        dialog?.[0] ?? null;
      const form = root?.querySelector("form");
      if (!form) return false;
      const parsed = page.parse(form);
      if (parsed.error) {
        ui.notifications?.warn(parsed.error);
        return false;
      }
      result = { action: page.isLast ? "submit" : "next", patch: parsed.patch || {} };
      return page.isLast ? "submit" : "next";
    },
  });
  await DialogV2.wait({
    window: { title: page.title || "Create your character" },
    position: { width: 620 },
    content,
    rejectClose: false,
    render: (event, dialog) => {
      const root =
        dialog?.element instanceof HTMLElement ? dialog.element :
        event?.target instanceof HTMLElement ? event.target : null;
      try {
        constrainDialogToViewport(root);
        page.wire?.(root);
      } catch (err) {
        console.warn("AI DM Bridge: wizard page wiring failed", err);
      }
    },
    buttons,
  });
  return result;
}

async function runPagedWizard(payload, pcId, userInfo, sendEventToPython, DialogV2) {
  // Seed state from any previous answers (re-prompt after validation
  // errors comes back through the same payload shape as v1).
  const prev = payload.previous_answers || {};
  let state = {
    name: prev.name || "",
    archetype: String(prev.archetype || "").toLowerCase() || ((payload.archetypes || [])[0]?.key ?? ""),
    stat_bump: prev.stat_bump || null,
    background: String(prev.background || "").toLowerCase() || ((payload.backgrounds || [])[0]?.key ?? ""),
    purchases: Array.isArray(prev.purchases) ? prev.purchases.slice() : [],
    cantrip_picks: Array.isArray(prev.cantrip_picks) ? prev.cantrip_picks.slice() : [],
    spell_picks: Array.isArray(prev.spell_picks) ? prev.spell_picks.slice() : [],
  };

  // Page descriptors. ``skip`` is evaluated lazily so the equipment
  // and spells pages adapt to the currently-selected archetype.
  const allPages = [
    { key: "identity",   title: "Name your character",  render: _renderIdentity,   parse: _readIdentity,   wire: null },
    { key: "archetype",  title: "Pick an archetype",    render: _renderArchetype,  parse: _readArchetype,  wire: null },
    { key: "stats",      title: "Adjust your stats",    render: _renderStats,      parse: _readStats,      wire: null },
    { key: "background", title: "Choose a background",  render: _renderBackground, parse: _readBackground, wire: null },
    {
      key: "equipment",  title: "Starting equipment",   render: _renderEquipment,  parse: _readEquipment,  wire: _wireEquipment,
      skip: (s) => !_archetypeNeedsShopping(payload, s.archetype),
    },
    {
      key: "spells",     title: "Pick your spells",     render: _renderSpells,     parse: _readSpells,     wire: _wireSpells,
      skip: (s) => !_archetypeIsCaster(payload, s.archetype),
    },
    { key: "summary",    title: "Review",               render: _renderSummary,    parse: () => ({ patch: {} }), wire: null },
  ];

  // Walk forward/back through the page list, skipping pages whose
  // ``skip(state)`` predicate is true at visit time. ``visited`` tracks
  // the back-stack so Back rewinds across previously-skipped pages too.
  const stack = [];
  let i = 0;
  let cancelled = false;
  let dismissed = false;
  while (i < allPages.length) {
    const page = allPages[i];
    if (page.skip && page.skip(state)) {
      i += 1;
      continue;
    }
    // Annotate the page for the renderer (step counter + button set).
    const visiblePages = allPages.filter((p) => !(p.skip && p.skip(state)));
    const stepIndex = visiblePages.findIndex((p) => p.key === page.key) + 1;
    state._stepIndex = stepIndex;
    state._totalSteps = visiblePages.length;
    const annotated = {
      ...page,
      canBack: stack.length > 0,
      isLast: stepIndex === visiblePages.length,
    };
    const result = await _runPage(DialogV2, payload, state, annotated);
    if (result.action === "cancel") { cancelled = true; break; }
    if (result.action === "dismissed") { dismissed = true; break; }
    if (result.action === "back") {
      i = stack.pop() ?? 0;
      continue;
    }
    Object.assign(state, result.patch || {});
    if (result.action === "submit") break;
    stack.push(i);
    i += 1;
  }

  // Strip transient annotations before sending back to Python.
  delete state._stepIndex;
  delete state._totalSteps;

  if (cancelled) {
    console.log("AI DM Bridge: paged wizard skipped — sending cancelled response");
    sendEventToPython("wizard_response", { pc_id: pcId, cancelled: true, ...userInfo });
    return;
  }
  if (dismissed) {
    // User closed the window mid-flow — don't tell Python they cancelled;
    // Python re-emits ``wizard_request`` shortly and we'll re-open then.
    console.log("AI DM Bridge: paged wizard dismissed without an answer — awaiting next wizard_request");
    ui.notifications?.info("Character creation is required to start. The dialog will reopen shortly.");
    return;
  }
  console.log("AI DM Bridge: paged wizard submitted, sending wizard_response", {
    pc_id: pcId, user_id: userInfo.user_id, user_name: userInfo.user_name, ...state,
  });
  sendEventToPython("wizard_response", { pc_id: pcId, ...userInfo, ...state });
}

