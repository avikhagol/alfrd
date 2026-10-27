import test from 'node:test';
import assert from 'node:assert/strict';
import { mountPanelResize, panelWidth } from '../../src/alfrd/web/js/components/panel_resize.js';
class Handle extends EventTarget {
  attrs = {}; classList = { add() {}, toggle() {} };
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  focus() {}
  setPointerCapture(id) { this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture() { this.capture = null; }
}
const fire = (h, type, data = {}) => h.dispatchEvent(Object.assign(new Event(type, { cancelable: true }), data));
function setup(side = 'left', initial = 220) {
  globalThis.window = new EventTarget();
  const saved = []; let applied, room = 360, enabled = true;
  const h = new Handle();
  const controller = mountPanelResize({ panel: { id: 'panel' }, handle: h, width: initial, min: 160, max: 360, defaultWidth: 220, side,
    label: 'Resize sessions', available: () => room, enabled: () => enabled, apply: w => { applied = w; }, save: w => saved.push(w) });
  return { h, controller, saved, applied: () => applied, room(w) { room = w; controller.sync(); }, enabled(v) { enabled = v; controller.sync(); } };
}

test('stored widths accept finite numbers only and clamp valid values', () => {
  for (const value of [null, undefined, '250', NaN, Infinity, {}, true]) assert.equal(panelWidth(value, 160, 360, 220), 220);
  assert.equal(panelWidth(100, 160, 360, 220), 160); assert.equal(panelWidth(900, 160, 360, 220), 360);
});

test('physical boundary direction, keyboard steps, limits and reset save immediately', () => {
  for (const side of ['left', 'right']) {
    const c = setup(side); const key = k => fire(c.h, 'keydown', { key: k });
    assert.equal(c.h.attrs['aria-controls'], 'panel'); assert.equal(c.h.attrs.role, 'separator');
    key('ArrowRight'); assert.equal(c.applied(), side === 'left' ? 240 : 200);
    fire(c.h, 'keydown', { key: 'ArrowLeft', shiftKey: true }); assert.equal(c.applied(), side === 'left' ? 160 : 280);
    key('End'); assert.equal(c.applied(), 360); key('Home'); assert.equal(c.applied(), 160);
    key('Enter'); assert.equal(c.applied(), 220); key('End'); fire(c.h, 'dblclick'); assert.equal(c.applied(), 220);
    assert.equal(c.h.attrs['aria-valuetext'], '220 pixels'); assert.equal(c.saved.at(-1), 220); c.controller.destroy();
  }
});

test('pointer drag uses boundary deltas and commits only on pointerup; all cancellation paths restore preference', () => {
  for (const side of ['left', 'right']) {
    for (const cancel of ['pointercancel', 'lostpointercapture', 'Escape', 'pointerup']) {
      const c = setup(side);
      fire(c.h, 'pointerdown', { pointerId: 7, clientX: 440, button: 0 });
      assert.equal(c.h.capture, 7);
      fire(c.h, 'pointermove', { pointerId: 7, clientX: 480 }); assert.equal(c.applied(), side === 'left' ? 260 : 180);
      assert.deepEqual(c.saved, []);
      if (cancel === 'Escape') fire(c.h, 'keydown', { key: cancel }); else fire(c.h, cancel, { pointerId: 7 });
      assert.equal(c.applied(), cancel === 'pointerup' ? (side === 'left' ? 260 : 180) : 220);
      assert.equal(c.saved.length, cancel === 'pointerup' ? 1 : 0);
      fire(c.h, 'pointermove', { pointerId: 7, clientX: 600 }); assert.equal(c.applied(), cancel === 'pointerup' ? (side === 'left' ? 260 : 180) : 220);
      c.controller.destroy();
    }
  }
});

test('viewport and collapsed modes retain preferred width; destroy removes listeners and cancels drags', () => {
  const c = setup('left', 340); c.room(200); assert.equal(c.applied(), 200); assert.equal(c.controller.preferred(), 340); assert.deepEqual(c.saved, []);
  c.enabled(false); assert.equal(c.h.hidden, true); assert.equal(c.h.tabIndex, -1);
  fire(c.h, 'keydown', { key: 'Home' }); assert.equal(c.controller.preferred(), 340);
  c.enabled(true); c.room(360); assert.equal(c.applied(), 340); assert.equal(c.h.tabIndex, 0);
  fire(c.h, 'pointerdown', { pointerId: 1, clientX: 0 }); fire(c.h, 'pointermove', { pointerId: 1, clientX: -100 });
  c.controller.destroy(); assert.equal(c.applied(), 340); assert.deepEqual(c.saved, []);
  fire(c.h, 'keydown', { key: 'Home' }); assert.equal(c.applied(), 340);
});

test('blocked localStorage does not prevent resizing', () => {
  globalThis.window = new EventTarget(); globalThis.localStorage = { getItem() { throw Error('denied'); }, setItem() { throw Error('denied'); } };
  const h = new Handle(); let applied;
  const c = mountPanelResize({ panel: { id: 'p' }, handle: h, key: 'width', min: 160, max: 360, defaultWidth: 220, apply: w => { applied = w; } });
  fire(h, 'keydown', { key: 'End' }); assert.equal(applied, 360); c.destroy(); delete globalThis.localStorage;
});

test('saved width survives a fresh mount with no duplicate handlers', () => {
  const values = new Map();
  globalThis.window = new EventTarget();
  globalThis.localStorage = { getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
  const h = new Handle(); let applied;
  const mount = () => mountPanelResize({ panel: { id: 'p' }, handle: h, key: 'ui:chat.sessionsWidth', min: 160, max: 360, defaultWidth: 220, apply: w => { applied = w; } });
  let c = mount(); fire(h, 'keydown', { key: 'ArrowRight' }); assert.equal(applied, 240); c.destroy();
  c = mount(); assert.equal(applied, 240); fire(h, 'keydown', { key: 'ArrowRight' }); assert.equal(applied, 260);
  assert.equal(values.get('alfrd-studio:ui:chat.sessionsWidth'), '260'); c.destroy(); delete globalThis.localStorage;
});
