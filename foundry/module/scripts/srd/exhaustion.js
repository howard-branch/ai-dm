/** SRD 5.2 (2024) one-track exhaustion — mirrors `ai_dm.rules.exhaustion`. */
import { srd } from "./core_loader.js";

function cfg() {
    return srd()?.exhaustion ?? {
        max_level: 6, death_at: 6,
        per_level: { d20_penalty: -2, speed_penalty_ft: -5 },
    };
}

export function clampLevel(level) {
    const max = cfg().max_level;
    return Math.max(0, Math.min(max, level | 0));
}

export function add(level, n = 1) { return clampLevel((level | 0) + (n | 0)); }
export function remove(level, n = 1) { return clampLevel((level | 0) - (n | 0)); }

export function d20Penalty(level) {
    return clampLevel(level) * cfg().per_level.d20_penalty;
}
export function speedPenalty(level) {
    return clampLevel(level) * cfg().per_level.speed_penalty_ft;
}
export function isDead(level) { return clampLevel(level) >= cfg().death_at; }

