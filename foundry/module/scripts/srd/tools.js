/** Tools — mirrors `ai_dm.rules.tools`. */
import { srd } from "./core_loader.js";

function _tools() { return srd()?.tools?.tools ?? []; }

export function getTool(key) { return _tools().find((t) => t.key === key) ?? null; }
export function allTools()   { return [..._tools()]; }

export function isProficient(toolKey, proficiencies = []) {
    const rec = getTool(toolKey);
    if (!rec) return false;
    const list = proficiencies || [];
    return list.includes(rec.proficiency_group) || list.includes(toolKey);
}

