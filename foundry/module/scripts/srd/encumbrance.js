/** Carrying capacity & encumbrance — mirrors `ai_dm.rules.encumbrance`. */
import { srd } from "./core_loader.js";

function _data() { return srd()?.encumbrance ?? {}; }

export function carryingCapacity(str) {
    return Number(str) * Number(_data().carrying_capacity_per_str ?? 15);
}

export function pushDragLift(str) {
    return Number(str) * Number(_data().push_drag_lift_per_str ?? 30);
}

export function encumbranceStatus(weightLb, str, { variant = false } = {}) {
    const d = _data();
    const v = d.variant ?? {};
    const w = Number(weightLb);
    const s = Number(str);
    if (variant) {
        if (w > Number(v.heavily_encumbered_per_str ?? 10) * s) return "heavy";
        if (w > Number(v.encumbered_per_str ?? 5) * s) return "encumbered";
        return "normal";
    }
    if (w > Number(d.carrying_capacity_per_str ?? 15) * s) return "heavy";
    return "normal";
}

export function speedPenalty(status) {
    const v = _data().variant ?? {};
    if (status === "encumbered") return Number(v.encumbered_speed_penalty_ft ?? -10);
    if (status === "heavy") return Number(v.heavily_encumbered_speed_penalty_ft ?? -20);
    return 0;
}

