/** Opportunity attacks — mirrors `ai_dm.rules.opportunity_attack`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.opportunity_attacks ?? {
        trigger: "leaves_reach_without_disengage",
        uses: "reaction",
        blockers: ["disengaging", "incapacitated", "speed_zero"],
    };
}

export function BLOCKERS() {
    return [..._data().blockers];
}

const _DENY = new Set(["incapacitated", "stunned", "paralyzed", "unconscious"]);

export function provokes(mover, { moverDisengaging } = {}) {
    const dis = (moverDisengaging !== undefined) ? Boolean(moverDisengaging) : Boolean(mover?.disengaging);
    return !dis;
}

export function canReact(defender) {
    if (!defender) return false;
    if (defender.reaction_used) return false;
    const cs = new Set(defender.conditions || []);
    for (const k of _DENY) if (cs.has(k)) return false;
    if (Number(defender.speed ?? 30) <= 0) return false;
    return true;
}

export function eligibleReactors(mover, threats) {
    if (!provokes(mover)) return [];
    return [...threats].filter(canReact).map((t) => t.actor_id);
}

export function consumeReaction(defender) {
    if (defender?.reaction_used) return false;
    if (defender) defender.reaction_used = true;
    return true;
}

