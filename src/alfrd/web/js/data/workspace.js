// Per-project workspaces, keyed by the backend project identifier ("all" is its own
// workspace). Views keep their UI state in `scoped(name, init)` objects, which always
// read and write the active project's copy, so switching projects never shows another
// project's filters, selections or drafts. Async work captures `owner()` when it starts
// and checks it before touching the screen: a proxy alone cannot stop a late response.

const spaces = new Map(); // project key -> { [name]: state object, meta: {...} }
const inits = new Map(); // name -> init()
let active = "all";

const keyOf = (project) => (project == null || project === "" ? "all" : String(project));

function space(project) {
  const k = keyOf(project);
  if (!spaces.has(k)) spaces.set(k, { meta: { target: null, view: null, scroll: {}, dirty: new Set() } });
  return spaces.get(k);
}

function slot(project, name) {
  const s = space(project);
  if (!(name in s)) s[name] = inits.get(name)();
  return s[name];
}

export function activeKey() { return active; }

/** Make `project` the active workspace; returns the outgoing key. */
export function setActive(project) {
  const before = active;
  active = keyOf(project);
  return before;
}

/** Workspace bookkeeping (last target, last tab, scroll positions, unsaved-edit flags). */
export function meta(project = active) { return space(project).meta; }

/** A state object that always resolves to the active project's copy of `name`. */
export function scoped(name, init) {
  inits.set(name, init);
  return new Proxy({}, {
    get: (_t, prop) => slot(active, name)[prop],
    set: (_t, prop, value) => { slot(active, name)[prop] = value; return true; },
    has: (_t, prop) => prop in slot(active, name),
    deleteProperty: (_t, prop) => delete slot(active, name)[prop],
    ownKeys: () => Reflect.ownKeys(slot(active, name)),
    getOwnPropertyDescriptor: (_t, prop) => {
      const d = Object.getOwnPropertyDescriptor(slot(active, name), prop);
      return d && { ...d, configurable: true };
    },
  });
}

/** The state object `name` of one specific project (for late responses and caches). */
export function stateOf(project, name) { return slot(project, name); }

/** Capture the originating workspace; `still()` is false once the user switched away. */
export function owner(project = active) {
  const k = keyOf(project);
  return { project: k, still: () => active === k };
}

export function markDirty(project, what, on = true) {
  const d = meta(project).dirty;
  if (on) d.add(what); else d.delete(what);
}
export function isDirty(project) { return meta(project).dirty.size > 0; }

/** Forget one project's workspace entirely (after removal). */
export function dropWorkspace(project) { spaces.delete(keyOf(project)); }

export function workspaceKeys() { return [...spaces.keys()]; }
