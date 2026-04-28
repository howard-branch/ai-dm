/** Attunement caps — mirrors `ai_dm.rules.attunement`. */
import { srd } from "./core_loader.js";

export const MAX_ATTUNED = 3;

export function maxAttuned() {
    return Number(srd()?.attunement?.max_attuned ?? MAX_ATTUNED);
}

export function canAttune(currentlyAttuned = []) {
    return (currentlyAttuned?.length ?? 0) < maxAttuned();
}

