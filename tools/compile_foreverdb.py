#!/usr/bin/env python3
"""Build the OpenXP_ForeverData addon from ForeverDB's addon data.

OpenXP ships no quest database. This writes a separate addon, under
ForeverDB's licence (GPL-3.0 with an attribution term), that OpenXP reads
in game when it's installed: which quests a quest needs first, the level
it's offered from and who gives it, for every quest the guides use and the
quests those need.

  1. Get the ForeverDB addon (https://foreverdb.net/downloads/addon-manifest.json
     lists the current zip and its SHA-256) and unzip it.
  2. python3 tools/compile_foreverdb.py --foreverdb path/to/ForeverDB --out dist

That writes dist/OpenXP_ForeverData/. Don't put it in this repository:
OpenXP is CC BY-NC-SA 4.0 and the data is GPL-3.0.

Every rule keeps where it comes from:
  foreverdb_verified  WoW Forever's own data, or a Classic rule that players'
                      ForeverDB addons also saw hold in WoW Forever
  cmangos_classic     Classic data (CMaNGOS classic-db), not confirmed in
                      WoW Forever
"""
import argparse
import glob
import os
import re
import shutil
import sys
from typing import NoReturn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = "OpenXP_ForeverData"
VERIFIED = "foreverdb_verified"
CLASSIC = "cmangos_classic"
CREDIT_LINE = re.compile(r"^-- Data: ForeverDB\.net \(https://foreverdb\.net\)")
ENTRY = re.compile(r'^\[(\d+)\]="((?:[^"\\]|\\.)*)",?\s*$')
QUEST_LINE = re.compile(r"^\s*\.(?:accept|turnin|complete)\s+-?(\d+)", re.M)
REF = re.compile(r"^([noi])(\d+)$")


def fail(message) -> "NoReturn":
    print("compile_foreverdb: " + message, file=sys.stderr)
    sys.exit(1)


def read(path):
    if not os.path.isfile(path):
        fail("missing " + path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def string_table(text, name):
    """The {[id] = "..."} table assigned to ns.<name>, as {id: string}."""
    out, inside = {}, False
    for line in text.splitlines():
        if not inside:
            inside = line.startswith("ns.%s = {" % name)
            continue
        if line.startswith("}"):
            return out
        m = ENTRY.match(line)
        if m:
            out[int(m.group(1))] = re.sub(r"\\(.)", r"\1", m.group(2))
    if not inside:
        fail("no ns.%s table found: has ForeverDB's data format changed?" % name)
    return out


def ids(text):
    return [int(x) for x in re.findall(r"\d+", text or "")]


def number(text):
    return int(text) if (text or "").isdigit() else 0


def first_ref(text):
    for word in (text or "").split():
        m = REF.match(word)
        if m:
            return m.group(1), int(m.group(2))
    return None


def load_foreverdb(src):
    classic_text = read(os.path.join(src, "Data", "QuestClassic.lua"))
    forever_text = read(os.path.join(src, "Data", "QuestForever.lua"))
    meta_text = read(os.path.join(src, "Data", "Meta.lua"))
    credit = forever_text.splitlines()[0] if forever_text else ""
    if not CREDIT_LINE.match(credit):
        fail("Data/QuestForever.lua doesn't start with ForeverDB's credit line")
    meta = {}
    for key in ("version", "build"):
        m = re.search(r'\b%s="([^"]*)"' % key, meta_text)
        if not m:
            fail("no %s in Data/Meta.lua" % key)
        meta[key] = m.group(1)
    groups = {}
    if "ns.qcExcl = {" in classic_text:
        groups = {g: ids(s) for g, s in string_table(classic_text, "qcExcl").items()}
    return {
        "classic": string_table(classic_text, "qcQuests"),
        "forever": string_table(forever_text, "qfQuests"),
        "groups": groups,
        "credit": credit,
        "meta": meta,
    }


def guide_quest_ids(guides):
    found = set()
    for path in sorted(glob.glob(os.path.join(guides, "**", "*.lua"), recursive=True)):
        with open(path, encoding="utf-8") as f:
            found.update(int(x) for x in QUEST_LINE.findall(f.read()))
    return found


def quest(db, qid):
    """One quest's rules with their sources, or None when ForeverDB has nothing on it."""
    cs, fs = db["classic"].get(qid), db["forever"].get(qid)
    if cs is None and fs is None:
        return None
    c = (cs or "").split("|") + [""] * 14
    f = (fs or "").split("|") + [""] * 11
    q = {}

    # level it's offered from: Forever's own value, else Classic's unless
    # players finished the quest below it in Forever
    classic_min, forever_min, seen_min = number(c[2]), number(f[2]), number(f[9])
    if forever_min > 0:
        q["min"], q["minSrc"] = forever_min, VERIFIED
    elif classic_min > 0 and not 0 < seen_min < classic_min:
        q["min"], q["minSrc"] = classic_min, CLASSIC

    need_all, need_any, group = ids(c[6]), ids(c[7]), number(c[8])
    excl = [x for x in db["groups"].get(group, []) if x != qid] if group > 0 else []
    after = number(f[8])
    if need_all:
        q["all"] = need_all
    if need_any:
        q["any"] = need_any
    if excl:
        q["excl"] = excl
    if need_all or need_any or excl:
        # confirmed only when the whole rule is the one quest players were
        # offered this right after in Forever
        confirmed = need_all == [after] and not need_any and not excl
        q["preSrc"] = VERIFIED if confirmed else CLASSIC
    elif after > 0:
        # an order players saw, not a rule: kept apart from the rules
        q["after"] = after

    giver, source = first_ref(f[4]), VERIFIED
    if not giver:
        giver, source = first_ref(c[9]), CLASSIC
    if giver:
        q["giverType"], q["giver"], q["giverSrc"] = giver[0], giver[1], source
    return q


def compile_quests(db, wanted):
    """{id: rules} for the wanted quests and every quest their rules name."""
    out, todo = {}, sorted(wanted)
    seen = set(todo)
    while todo:
        qid = todo.pop()
        q = quest(db, qid)
        if q is None:
            continue
        out[qid] = q
        for other in q.get("all", []) + q.get("any", []):
            if other not in seen:
                seen.add(other)
                todo.append(other)
    return out


def lua_list(values):
    return "{" + ",".join(str(v) for v in values) + "}"


def lua_quest(q):
    parts = []
    if "min" in q:
        parts.append("min=%d,minSrc=%s" % (q["min"], "V" if q["minSrc"] == VERIFIED else "C"))
    for key in ("all", "any", "excl"):
        if key in q:
            parts.append("%s=%s" % (key, lua_list(q[key])))
    if "preSrc" in q:
        parts.append("preSrc=%s" % ("V" if q["preSrc"] == VERIFIED else "C"))
    if "after" in q:
        parts.append("after=%d" % q["after"])
    if "giver" in q:
        parts.append('giver=%d,giverType="%s",giverSrc=%s' %
                     (q["giver"], q["giverType"], "V" if q["giverSrc"] == VERIFIED else "C"))
    return "{" + ",".join(parts) + "}"


def data_lua(db, quests):
    meta = db["meta"]
    lines = [
        db["credit"],
        "-- Quest rules for OpenXP: for each quest the level it's offered from (min), the quests that all have to be done",
        "-- first (all), of which one has to be done first (any), the quests that lock it out (excl), the quest players were",
        "-- offered it after when no rule is known (after) and who gives it (giver; giverType n npc, o object, i item).",
        "-- minSrc, preSrc and giverSrc say where each comes from:",
        '--   "%s": WoW Forever data, or a Classic rule players also saw hold in WoW Forever' % VERIFIED,
        '--   "%s": Classic data (CMaNGOS classic-db), not confirmed in WoW Forever' % CLASSIC,
        "-- Generated from ForeverDB's Data/QuestForever.lua and Data/QuestClassic.lua by OpenXP's",
        "-- tools/compile_foreverdb.py. Licensed under GPL-3.0, see LICENSE.txt and NOTICE.txt. Do not edit by hand.",
        'local V, C = "%s", "%s"' % (VERIFIED, CLASSIC),
        "OpenXP_ForeverData = {",
        "format=1,",
        'version="%s",' % meta["version"],
        'build="%s",' % meta["build"],
        'credit="Data from ForeverDB.net (https://foreverdb.net)",',
        "quests={",
    ]
    for qid in sorted(quests):
        lines.append("[%d]=%s," % (qid, lua_quest(quests[qid])))
    lines += ["},", "}", ""]
    return "\n".join(lines)


def toc(meta):
    return "\n".join([
        "## Interface: 16001",
        "## Title: OpenXP Forever Data",
        "## Notes: Quest chains and quest levels for OpenXP in WoW Forever. Data from ForeverDB.net (https://foreverdb.net).",
        "## Author: OpenXP contributors, data by ForeverDB.net",
        "## Version: %s" % meta["version"],
        "## X-License: GPL-3.0 with an attribution term, see LICENSE.txt and NOTICE.txt",
        "## X-Website: https://foreverdb.net",
        "",
        "Data.lua",
        "",
    ])


def notice(meta, count):
    return """OpenXP Forever Data - where this addon's data comes from, and under which terms
================================================================================

Data from ForeverDB.net (https://foreverdb.net)

This addon is one data file, Data.lua, for the OpenXP leveling guide addon. It
holds, for %(count)d quests, which quests have to be done first, the level a
quest is offered from and who gives it. It is not part of OpenXP and OpenXP
works without it.

1. Source
   Data.lua is derived from Data/QuestForever.lua and Data/QuestClassic.lua of
   the ForeverDB addon by ForeverDB.net, data version %(version)s, WoW Forever
   client build %(build)s. Copyright (C) 2026 ForeverDB.net. Licensed under the
   GNU General Public License version 3 with ForeverDB's attribution term
   (GPL-3.0 section 7(b)): see LICENSE.txt, which is ForeverDB's licence file
   unchanged, and COPYING.txt for the GPL's full text. This addon is under the
   same terms.

2. Classic data from CMaNGOS classic-db (GPL-3.0)
   Rules and givers marked "cmangos_classic" come, through ForeverDB's
   Data/QuestClassic.lua, from CMaNGOS classic-db
   (https://github.com/cmangos/classic-db), the open World of Warcraft Classic
   1.12 world database, Copyright (C) the CMaNGOS project and its contributors,
   licensed under the GNU General Public License v3.0. They are Classic data
   and may differ in WoW Forever.

3. What was changed
   Only the quests OpenXP's guides use, and the quests those need, were kept.
   Of each quest only the fields listed at the top of Data.lua were kept, and a
   source was written next to each. The file is generated by
   tools/compile_foreverdb.py in OpenXP's repository
   (https://github.com/mjared123/OpenXP); that script and ForeverDB's two data
   files are everything needed to rebuild it.

4. World of Warcraft
   World of Warcraft, WoW Forever and their names are trademarks or property of
   Blizzard Entertainment, Inc. This addon is not affiliated with or endorsed
   by Blizzard Entertainment, ForeverDB.net or the CMaNGOS project.
""" % {"count": count, "version": meta["version"], "build": meta["build"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the OpenXP_ForeverData addon from ForeverDB's addon data.")
    ap.add_argument("--foreverdb", required=True, help="the unzipped ForeverDB addon folder (holds Data/ and LICENSE.txt)")
    ap.add_argument("--out", required=True, help="folder to write %s/ into (not inside this repository)" % ADDON)
    ap.add_argument("--guides", default=os.path.join(ROOT, "Guides"), help="OpenXP's Guides folder")
    args = ap.parse_args(argv)

    licences = [os.path.join(args.foreverdb, name) for name in ("LICENSE.txt", "COPYING.txt")]
    for path in licences:
        if not os.path.isfile(path):
            fail("missing %s: the data can't be passed on without ForeverDB's licence files" % path)
    db = load_foreverdb(args.foreverdb)
    wanted = guide_quest_ids(args.guides)
    if not wanted:
        fail("no quests found in " + args.guides)
    quests = compile_quests(db, wanted)

    addon = os.path.join(args.out, ADDON)
    os.makedirs(addon, exist_ok=True)
    for path in licences:
        shutil.copyfile(path, os.path.join(addon, os.path.basename(path)))
    files = {
        "Data.lua": data_lua(db, quests),
        ADDON + ".toc": toc(db["meta"]),
        "NOTICE.txt": notice(db["meta"], len(quests)),
    }
    for name, text in files.items():
        with open(os.path.join(addon, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    rules = sum(1 for q in quests.values() if "preSrc" in q)
    verified = sum(1 for q in quests.values() if q.get("preSrc") == VERIFIED)
    print("%s: %d quests (%d in guides, %d without ForeverDB data), %d with rules (%d seen in Forever, %d Classic data)"
          % (addon, len(quests), len(wanted), len(wanted - set(quests)), rules, verified, rules - verified))
    return 0


if __name__ == "__main__":
    sys.exit(main())
