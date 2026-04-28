/** Areas of effect — mirrors `ai_dm.rules.areas_of_effect`. */
import { srd } from "./core_loader.js";

function _data() {
    return srd()?.areas_of_effect ?? {
        shapes: [
            { key: "sphere" }, { key: "cube" }, { key: "cone" },
            { key: "line", width_ft: 5 }, { key: "cylinder" },
        ],
    };
}

export function SHAPES() {
    return _data().shapes.map((s) => s.key);
}

export const LINE_DEFAULT_WIDTH_FT = 5;

export function pointsInSphere(points, { center, radiusFt }) {
    const [cx, cy] = center;
    return points.filter((p) => Math.hypot(p[0] - cx, p[1] - cy) <= radiusFt);
}

export function pointsInCube(points, { origin, sideFt }) {
    const [ox, oy] = origin;
    return points.filter((p) => p[0] >= ox && p[0] <= ox + sideFt && p[1] >= oy && p[1] <= oy + sideFt);
}

export function pointsInCone(points, { apex, lengthFt, directionDeg, halfAngleDeg = 26.565 }) {
    const [ax, ay] = apex;
    const dirx = Math.cos(directionDeg * Math.PI / 180);
    const diry = Math.sin(directionDeg * Math.PI / 180);
    const cosT = Math.cos(halfAngleDeg * Math.PI / 180);
    return points.filter((p) => {
        const dx = p[0] - ax, dy = p[1] - ay;
        const dist = Math.hypot(dx, dy);
        if (dist <= 0) return true;
        if (dist > lengthFt) return false;
        return (dx * dirx + dy * diry) / dist >= cosT;
    });
}

export function pointsInLine(points, { origin, lengthFt, directionDeg, widthFt = LINE_DEFAULT_WIDTH_FT }) {
    const [ox, oy] = origin;
    const dirx = Math.cos(directionDeg * Math.PI / 180);
    const diry = Math.sin(directionDeg * Math.PI / 180);
    const perpx = -diry, perpy = dirx;
    const halfW = widthFt / 2;
    return points.filter((p) => {
        const dx = p[0] - ox, dy = p[1] - oy;
        const along = dx * dirx + dy * diry;
        const across = Math.abs(dx * perpx + dy * perpy);
        return along >= 0 && along <= lengthFt && across <= halfW;
    });
}

