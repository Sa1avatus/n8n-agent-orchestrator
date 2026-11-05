/* board-scope-wrapped */
(function () {
"use strict";
/* Pure, DOM-free ordering rules for the waiting tasks of a mission (T002).
 * Follows the mockup rules in docs/board-views-mockup.html (lines 194-205).
 *
 * deps: { id: [dependency ids] } restricted to the ids it contains.
 * tasks: [{ id, st, deps }] in list order; st "g" means waiting, anything
 * else (running, accepted, rejected) is locked and never changes its slot.
 * An order is an array of ids; a violation is a pair [dependency, dependent].
 */

function parseTaskId(text) {
  var s = String(text);
  return /^T[0-9]+[A-Z]*$/.test(s) ? s : null;
}

function dependents(deps, id) {
  return Object.keys(deps).filter(function (k) {
    return deps[k].indexOf(id) >= 0;
  });
}

function closure(deps, id, direction) {
  var down = direction === "down";
  var seen = Object.create(null), out = [], stack = [id];
  while (stack.length) {
    var x = stack.pop();
    var next = down ? dependents(deps, x) : (deps[x] || []);
    next.forEach(function (n) {
      if (n !== id && !seen[n] && Array.isArray(deps[n])) {
        seen[n] = 1;
        out.push(n);
        stack.push(n);
      }
    });
  }
  return out;
}

function violations(order, deps) {
  var pos = Object.create(null), out = [];
  order.forEach(function (id, i) { pos[id] = i; });
  order.forEach(function (id) {
    (deps[id] || []).forEach(function (d) {
      if (pos[d] !== undefined && pos[d] > pos[id]) out.push([d, id]);
    });
  });
  return out;
}

function wouldCycle(deps, from, to) {
  if (from === to || closure(deps, from, "down").indexOf(to) >= 0) return [to, from];
  return null;
}

function moveOrder(order, id, toIndex) {
  var ids = Array.isArray(id) ? id : [id];
  var block = order.filter(function (x) { return ids.indexOf(x) >= 0; });
  var rest = order.filter(function (x) { return ids.indexOf(x) < 0; });
  var at = Math.min(Math.max(0, toIndex), rest.length);
  return rest.slice(0, at).concat(block, rest.slice(at));
}

function repairOrder(order, deps) {
  var left = order.slice(), placed = Object.create(null), inOrder = Object.create(null), out = [];
  order.forEach(function (id) { inOrder[id] = 1; });
  while (left.length) {
    var i = left.findIndex(function (id) {
      return (deps[id] || []).every(function (d) { return !inOrder[d] || placed[d]; });
    });
    if (i < 0) throw new Error("dependency cycle");
    placed[left[i]] = 1;
    out.push(left.splice(i, 1)[0]);
  }
  return out;
}

function tryMove(tasks, id, toIndex) {
  var byId = Object.create(null), wIds = [], deps = Object.create(null);
  tasks.forEach(function (t) {
    byId[t.id] = t;
    if (t.st === "g") wIds.push(t.id);
  });
  var waiting = Object.create(null);
  wIds.forEach(function (w) { waiting[w] = 1; });
  wIds.forEach(function (w) {
    deps[w] = byId[w].deps.filter(function (d) { return waiting[d]; });
  });
  function full(waitOrder) {
    var k = 0;
    return tasks.map(function (t) { return t.st === "g" ? waitOrder[k++] : t.id; });
  }
  if (!byId[id]) return { ok: false, reason: "unknown", broken: [], group: null };
  if (!waiting[id]) return { ok: false, reason: "locked", broken: [], group: null };

  var from = wIds.indexOf(id);
  var to = Math.min(Math.max(0, toIndex), wIds.length - 1);
  if (to === from) return { ok: true, order: full(wIds) };

  var plain = moveOrder(wIds, id, to);
  var broken = violations(plain, deps);
  if (!broken.length) return { ok: true, order: full(plain) };

  var down = to > from;
  var grp = [id].concat(closure(deps, id, down ? "down" : "up"));
  var groupOrder = moveOrder(wIds, grp, to);
  var groupBroken = violations(groupOrder, deps);
  if (groupBroken.length) return { ok: false, reason: "group-invalid", broken: groupBroken, group: null };
  return { ok: false, reason: "dependency", broken: broken, group: { ids: grp, order: full(groupOrder) } };
}

var BoardOrder = {
  parseTaskId: parseTaskId,
  closure: closure,
  violations: violations,
  wouldCycle: wouldCycle,
  moveOrder: moveOrder,
  repairOrder: repairOrder,
  tryMove: tryMove
};

globalThis.BoardOrder = BoardOrder;
if (typeof module !== "undefined" && module.exports) module.exports = BoardOrder;
})();
