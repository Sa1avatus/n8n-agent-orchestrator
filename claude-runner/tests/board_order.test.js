"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const BO = require(path.join(__dirname, "..", "src", "claude_runner", "board_order.js"));

function t(id, deps, st) {
  return { id: id, deps: deps || [], st: st || "g" };
}

function depsOf(tasks) {
  const waiting = {};
  tasks.forEach((x) => { if (x.st === "g") waiting[x.id] = 1; });
  const deps = {};
  tasks.forEach((x) => {
    if (x.st === "g") deps[x.id] = x.deps.filter((d) => waiting[d]);
  });
  return deps;
}

function waitingOrder(tasks) {
  return tasks.filter((x) => x.st === "g").map((x) => x.id);
}

function reorder(tasks, order) {
  const by = {};
  tasks.forEach((x) => { by[x.id] = x; });
  return order.map((id) => by[id]);
}

test("parseTaskId accepts T-ids with a letter suffix and rejects others", () => {
  assert.equal(BO.parseTaskId("T001"), "T001");
  assert.equal(BO.parseTaskId("T013A"), "T013A");
  assert.equal(BO.parseTaskId("T013AB"), "T013AB");
  assert.equal(BO.parseTaskId("t001"), null);
  assert.equal(BO.parseTaskId("T001 x"), null);
  assert.equal(BO.parseTaskId("T"), null);
});

test("chain: moving the root down pulls its dependents with it", () => {
  const tasks = [t("T1"), t("T2", ["T1"]), t("T3", ["T2"]), t("T4", ["T3"])];
  const r = BO.tryMove(tasks, "T1", 3);
  assert.equal(r.ok, false);
  assert.equal(r.reason, "dependency");
  assert.deepEqual(r.broken, [["T1", "T2"]]);
  assert.deepEqual(r.group.ids, ["T1", "T2", "T3", "T4"]);
  assert.deepEqual(r.group.order, ["T1", "T2", "T3", "T4"]);
});

test("chain: moving the last task to the front is refused, group pulls its dependencies", () => {
  const tasks = [t("T1"), t("T2", ["T1"]), t("T3", ["T2"]), t("T4", ["T3"])];
  const r = BO.tryMove(tasks, "T4", 0);
  assert.equal(r.ok, false);
  assert.deepEqual(r.broken, [["T3", "T4"]]);
  assert.deepEqual(r.group.ids, ["T4", "T3", "T2", "T1"]);
  assert.deepEqual(r.group.order, ["T1", "T2", "T3", "T4"]);
});

test("diamond: T4 needs T2 and T3, moving it to the front offers the whole closure", () => {
  const tasks = [t("T1"), t("T2", ["T1"]), t("T3", ["T1"]), t("T4", ["T2", "T3"])];
  const r = BO.tryMove(tasks, "T4", 0);
  assert.equal(r.ok, false);
  assert.deepEqual(r.group.ids.slice().sort(), ["T1", "T2", "T3", "T4"]);
  assert.deepEqual(r.group.order, ["T1", "T2", "T3", "T4"]);
});

test("two roots: an independent move is applied as is", () => {
  const tasks = [t("T1"), t("T2"), t("T3", ["T1"]), t("T4", ["T2"])];
  const r = BO.tryMove(tasks, "T2", 0);
  assert.deepEqual(r, { ok: true, order: ["T2", "T1", "T3", "T4"] });
});

test("moving the first task to the end with no dependents", () => {
  const tasks = [t("T1"), t("T2"), t("T3")];
  assert.deepEqual(BO.tryMove(tasks, "T1", 2), { ok: true, order: ["T2", "T3", "T1"] });
});

test("moving the last task to the front with no dependencies", () => {
  const tasks = [t("T1"), t("T2"), t("T3")];
  assert.deepEqual(BO.tryMove(tasks, "T3", 0), { ok: true, order: ["T3", "T1", "T2"] });
});

test("cycle attempt returns the closing pair and null when there is no cycle", () => {
  const deps = { T1: [], T2: ["T1"], T3: ["T2"] };
  assert.deepEqual(BO.wouldCycle(deps, "T1", "T3"), ["T3", "T1"]);
  assert.deepEqual(BO.wouldCycle(deps, "T1", "T1"), ["T1", "T1"]);
  assert.equal(BO.wouldCycle(deps, "T3", "T1"), null);
});

test("a group move that must pull another dependency: moving up pulls the dependency", () => {
  const tasks = [t("T1"), t("T2"), t("T3"), t("T4", ["T2"])];
  const r = BO.tryMove(tasks, "T4", 0);
  assert.equal(r.ok, false);
  assert.deepEqual(r.broken, [["T2", "T4"]]);
  assert.deepEqual(r.group.ids, ["T4", "T2"]);
  assert.deepEqual(r.group.order, ["T2", "T4", "T1", "T3"]);
});

test("a refused group move returns the broken pairs and no group", () => {
  const tasks = [t("T1"), t("T2", ["T1"]), t("T3"), t("T4"), t("T5", ["T1", "T4"])];
  const r = BO.tryMove(tasks, "T1", 1);
  assert.equal(r.ok, false);
  assert.equal(r.reason, "group-invalid");
  assert.equal(r.group, null);
  assert.deepEqual(r.broken, [["T4", "T5"]]);
});

test("locked tasks never change position", () => {
  const tasks = [t("T1"), t("T2", [], "p"), t("T3"), t("T4", [], "a"), t("T5")];
  const r = BO.tryMove(tasks, "T1", 2);
  assert.deepEqual(r, { ok: true, order: ["T3", "T2", "T5", "T4", "T1"] });
});

test("a locked task cannot be moved and an unknown id is refused", () => {
  const tasks = [t("T1"), t("T2", [], "f"), t("T3")];
  assert.deepEqual(BO.tryMove(tasks, "T2", 0), { ok: false, reason: "locked", broken: [], group: null });
  assert.deepEqual(BO.tryMove(tasks, "T9", 0), { ok: false, reason: "unknown", broken: [], group: null });
});

test("violations and moveOrder: block move keeps relative order", () => {
  const deps = { T1: [], T2: ["T1"], T3: [] };
  assert.deepEqual(BO.violations(["T2", "T1", "T3"], deps), [["T1", "T2"]]);
  assert.deepEqual(BO.moveOrder(["T1", "T2", "T3", "T4"], ["T2", "T4"], 0), ["T2", "T4", "T1", "T3"]);
});

test("repairOrder moves only what is needed and keeps the others in place", () => {
  const deps = { T1: [], T2: ["T1"], T3: [], T4: ["T3"] };
  assert.deepEqual(BO.repairOrder(["T2", "T1", "T4", "T3"], deps), ["T1", "T2", "T3", "T4"]);
  assert.deepEqual(BO.repairOrder(["T3", "T1", "T2"], deps), ["T3", "T1", "T2"]);
  assert.throws(() => BO.repairOrder(["T1", "T2"], { T1: ["T2"], T2: ["T1"] }), /cycle/);
});

function rng(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s + 0x6d2b79f5) >>> 0;
    let r = Math.imul(s ^ (s >>> 15), 1 | s);
    r = (r + Math.imul(r ^ (r >> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

test("200 seeded random DAGs and moves: no dependency violated, nothing lost or duplicated", () => {
  const rand = rng(20260518);
  const int = (n) => Math.floor(rand() * n);
  const shuffle = (a) => {
    const b = a.slice();
    for (let i = b.length - 1; i > 0; i--) {
      const j = int(i + 1);
      [b[i], b[j]] = [b[j], b[i]];
    }
    return b;
  };

  for (let c = 0; c < 200; c++) {
    const n = 2 + int(9);
    const names = Array.from({ length: n }, (_, i) => "T" + (i + 1));
    const rank = shuffle(names);
    const pos = {};
    rank.forEach((id, i) => { pos[id] = i; });
    let tasks = names.map((id) => {
      const deps = names.filter((d) => pos[d] < pos[id] && rand() < 0.3);
      return t(id, deps, rand() < 0.2 ? "p" : "g");
    });
    const wd = depsOf(tasks);
    const fixed = BO.repairOrder(shuffle(waitingOrder(tasks)), wd);
    let k = 0;
    tasks = shuffle(tasks).map((x) => {
      if (x.st !== "g") return x;
      const want = fixed[k++];
      return tasks.find((y) => y.id === want);
    });

    for (let m = 0; m < 5; m++) {
      const before = tasks.map((x) => x.id);
      const id = names[int(n)];
      const to = int(n);
      const r = BO.tryMove(tasks, id, to);
      const deps = depsOf(tasks);

      if (r.ok) {
        assert.deepEqual(r.order.slice().sort(), names.slice().sort(), "ids lost or duplicated");
        tasks.forEach((x, i) => {
          if (x.st !== "g") assert.equal(r.order[i], before[i], "locked task moved");
        });
        const w = r.order.filter((x) => tasks.find((y) => y.id === x).st === "g");
        assert.deepEqual(BO.violations(w, deps), [], "dependency violated after move");
        tasks = reorder(tasks, r.order);
      } else {
        assert.ok(Array.isArray(r.broken));
        if (r.group) {
          const g = r.group.order;
          assert.deepEqual(g.slice().sort(), names.slice().sort(), "group lost or duplicated ids");
          tasks.forEach((x, i) => {
            if (x.st !== "g") assert.equal(g[i], before[i], "locked task moved by group");
          });
          const gw = g.filter((x) => tasks.find((y) => y.id === x).st === "g");
          assert.deepEqual(BO.violations(gw, deps), [], "group move violates a dependency");
          if (rand() < 0.5) tasks = reorder(tasks, g);
        }
      }
    }
  }
});
