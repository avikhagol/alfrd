// Decorative outlines share geometry with the initial HTML. No per-row announcements.
export const skeletonRows = (kind, count = 3) => `<div class="skeleton-rows skeleton-${kind}" aria-hidden="true">${Array.from({ length: count }, () => '<div class="skeleton-block"></div>').join("")}</div>`;
export const outlineHtml = () => document.querySelector("#loading-outline").innerHTML;
