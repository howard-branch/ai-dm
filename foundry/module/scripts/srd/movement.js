/** Movement — mirrors `ai_dm.rules.movement`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.movement ?? {
        default_speed_ft: 30,
        modes: ["walk", "fly", "swim", "climb", "burrow"],
        difficult_terrain_factor: 2,
        prone_crawl_factor: 2,
        climbing_factor: 2,
        swimming_factor: 2,
        standing_costs_half_speed: true,
    };
}

export const MOVEMENT_MODES = ["walk", "fly", "swim", "climb", "burrow"];

export function cost(distanceFt, { difficult = false, climbing = false, swimming = false, crawling = false } = {}) {
    const d = _data();
    let factor = 1;
    if (difficult) factor *= Number(d.difficult_terrain_factor || 2);
    if (climbing) factor *= Number(d.climbing_factor || 2);
    if (swimming) factor *= Number(d.swimming_factor || 2);
    if (crawling) factor *= Number(d.prone_crawl_factor || 2);
    return Number(distanceFt) * factor;
}

export function budget(actor) {
    let base = Number(actor?.speed ?? _data().default_speed_ft);
    if (actor?.dashed) base *= 2;
    return Math.max(0, base);
}

export function remaining(actor) {
    return Math.max(0, budget(actor) - Number(actor?.movement_used ?? 0));
}

export function spend(actor, distanceFt, opts = {}) {
    const need = cost(distanceFt, opts);
    if (need > remaining(actor)) {
        throw new Error(`insufficient movement: need ${need}, have ${remaining(actor)}`);
    }
    actor.movement_used = Number(actor.movement_used ?? 0) + need;
    return remaining(actor);
}

