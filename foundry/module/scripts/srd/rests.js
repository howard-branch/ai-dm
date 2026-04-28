/** Rests & recovery — mirrors `ai_dm.rules.rests`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.rests ?? {
        short_rest: { duration_min: 60, hit_dice_spend: true, recovers: ["short_resources"] },
        long_rest: { duration_hr: 8, max_per_day: 1, recovers: ["hp_full", "short_resources", "long_resources", "spell_slots", "exhaustion_minus_1"] },
    };
}

export function SHORT_DURATION_MIN() { return Number(_data().short_rest.duration_min); }
export function LONG_DURATION_HR() { return Number(_data().long_rest.duration_hr); }
export function LONG_MAX_PER_DAY() { return Number(_data().long_rest.max_per_day); }

export function applyShortRest(actor, { hpHealed = 0, restored = [] } = {}) {
    if (hpHealed && actor) {
        actor.hp = Math.min(Number(actor.max_hp || actor.hp), Number(actor.hp || 0) + Number(hpHealed));
    }
    return { kind: "short", hp_restored: Number(hpHealed), resources_restored: [...restored] };
}

export function applyLongRest(actor) {
    if (!actor) return { kind: "long" };
    const before = Number(actor.hp || 0);
    actor.hp = Number(actor.max_hp || before);
    actor.temp_hp = 0;
    if (typeof actor.exhaustion === "number") {
        actor.exhaustion = Math.max(0, actor.exhaustion - 1);
    }
    return {
        kind: "long",
        hp_restored: actor.hp - before,
        exhaustion_after: actor.exhaustion ?? null,
    };
}

