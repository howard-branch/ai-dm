/** Turn structure — mirrors `ai_dm.rules.turn`. */
import { srd } from "./core_loader.js";

export const TURN_PHASES = [
    "start_of_turn", "action", "movement", "bonus_action", "end_of_turn",
];

function _data() {
    return srd()?.turn_structure ?? {
        phases: TURN_PHASES,
        reaction_resets_at: "start_of_turn",
        free_object_interactions_per_turn: 1,
    };
}

export function freeObjectInteractionsPerTurn() {
    return Number(_data().free_object_interactions_per_turn || 1);
}

export function reactionResetsAt() {
    return String(_data().reaction_resets_at || "start_of_turn");
}

