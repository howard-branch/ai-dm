/** Action economy — mirrors `ai_dm.rules.actions`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.actions ?? { economy_keys: [], standard_actions: [] };
}

export function ECONOMY_KEYS() {
    return [..._data().economy_keys];
}

export function ACTION_KEYS() {
    return _data().standard_actions.map((a) => a.key);
}

export function economyFor(key) {
    const rec = _data().standard_actions.find((a) => a.key === key);
    return rec ? rec.economy : "action";
}

const _ATTR = {
    action: "action_used",
    bonus_action: "bonus_action_used",
    reaction: "reaction_used",
};

export function isUsed(actor, slot) {
    if (slot === "free") return false;
    return Boolean(actor?.[_ATTR[slot]]);
}

export function consume(actor, slotOrAction) {
    if (!actor) return true;
    const known = _data().standard_actions.find((a) => a.key === slotOrAction);
    const slot = known ? known.economy : (slotOrAction || "action");
    if (slot === "free") return true;
    if (isUsed(actor, slot)) return false;
    actor[_ATTR[slot]] = true;
    return true;
}

