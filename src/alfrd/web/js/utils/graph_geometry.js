// Pure geometry shared by the chat and workflow graphs. No canvas state or DOM work.
export const NODE_H = 128;

export function edgePath(a, b) {
  if (Math.abs(a.y - b.y) < NODE_H / 2) {
    const leftToRight = a.x < b.x;
    const x1 = leftToRight ? a.x + a.w : a.x;
    const x2 = leftToRight ? b.x : b.x + b.w;
    const y1 = a.y + NODE_H / 2;
    const y2 = b.y + NODE_H / 2;
    const dx = Math.max(24, Math.abs(x2 - x1) / 2) * (leftToRight ? 1 : -1);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  }
  const x1 = a.x + a.w / 2;
  const y1 = a.y + NODE_H;
  const x2 = b.x + b.w / 2;
  const y2 = b.y;
  const dy = Math.max(30, (y2 - y1) / 2);
  return `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2 - 2}`;
}

