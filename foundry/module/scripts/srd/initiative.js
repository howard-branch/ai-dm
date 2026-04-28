/** Initiative — mirrors `ai_dm.rules.initiative`. */
import { srd } from "./core_loader.js";

export const INITIATIVE_ABILITY = "dex";

function _data() {
    return srd()?.initiative ?? { ability: "dex", surprise: { skip_first_turn: true } };
}

export function rollInitiative(actorId, { roll, modifier = 0, dexMod = 0 } = {}) {
    const r = Number(roll);
    return {
        actor_id: actorId,
        roll: r,
        modifier: Number(modifier),
        total: r + Number(modifier),
        dex_mod: Number(dexMod),
    };
}

export function sortOrder(rolls) {
    const items = [...rolls];
    items.sort((a, b) => {
        if (b.total !== a.total) return b.total - a.total;
        if (b.dex_mod !== a.dex_mod) return b.dex_mod - a.dex_mod;
        return String(a.actor_id).localeCompare(String(b.actor_id));
    });
    return items.map((r) => r.actor_id);
}

export function surpriseSkipsFirstTurn() {
    return Boolean(_data().surprise?.skip_first_turn);
}

