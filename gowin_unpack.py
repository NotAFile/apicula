import sys
import os
import re
import random
import numpy as np
from itertools import chain, count
import pickle
import codegen
import chipdb
from bslib import read_bitstream
from wirenames import wirenames

device = os.getenv("DEVICE")
if not device:
    raise Exception("DEVICE not set")

def parse_tile_(tiledata, tile):
    bels = {}
    for name, bel in tiledata.bels.items():
        for flag, bits in bel.flags.items():
            used_bits = {tile[row][col] for row, col in bits}
            if all(used_bits):
                bels.setdefault(name, set()).add(flag)
        mode_bits = {(row, col)
                     for row, col in bel.mode_bits
                     if tile[row][col] == 1}
        for mode, bits in bel.modes.items():
            if bits == mode_bits:
                bels.setdefault(name, set()).add(mode)

    pips = {}
    for dest, srcs in tiledata.pips.items():
        pip_bits = set().union(*srcs.values())
        used_bits = {(row, col)
                     for row, col in pip_bits
                     if tile[row][col] == 1}
        for src, bits in srcs.items():
            if bits == used_bits:
                pips[dest] = src

    return bels, pips


dffmap = {
    "DFF": None,
    "DFFS": "SET",
    "DFFR": "RESET",
    "DFFP": "PRESET",
    "DFFC": "CLEAR",
    "DFFNS": "SET",
    "DFFNR": "RESET",
    "DFFNP": "PRESET",
    "DFFNC": "CLEAR",
}
iobmap = {
    "IBUF": {"wires": ["O"], "inputs": ["I"]},
    "OBUF": {"wires": ["I"], "outputs": ["O"]},
    "IOBUF": {"wires": ["I", "O", "OEN"], "inouts": ["IO"]},
}
def tile2verilog(dbrow, dbcol, bels, pips, mod, db):
    # db is 0-based, floorplanner is 1-based
    row = dbrow+1
    col = dbcol+1
    aliases = db.grid[dbrow][dbcol].aliases
    for dest, src in chain(pips.items(), aliases.items()):
        srcg = chipdb.wire2global(row, col, db, src)
        destg = chipdb.wire2global(row, col, db, dest)
        mod.wires.update({srcg, destg})
        mod.assigns.append((destg, srcg))

    belre = re.compile(r"(IOB|LUT|DFF|BANK|CFG)(\w*)")
    for bel, flags in bels.items():
        typ, idx = belre.match(bel).groups()

        if typ == "LUT":
            val = sum(1<<f for f in flags)
            name = f"R{row}C{col}_LUT4_{idx}"
            lut = codegen.Primitive("LUT4", name)
            lut.params["INIT"] = f"16'b{val:016b}"
            lut.portmap['F'] = f"R{row}C{col}_F{idx}"
            lut.portmap['I0'] = f"R{row}C{col}_A{idx}"
            lut.portmap['I1'] = f"R{row}C{col}_B{idx}"
            lut.portmap['I2'] = f"R{row}C{col}_C{idx}"
            lut.portmap['I3'] = f"R{row}C{col}_D{idx}"
            mod.wires.update(lut.portmap.values())
            mod.primitives[name] = lut
        elif typ == "DFF":
            kind, = flags # DFF only have one flag
            idx = int(idx)
            port = dffmap[kind]
            name = f"R{row}C{col}_{typ}E_{idx}"
            dff = codegen.Primitive(kind+"E", name)
            dff.portmap['CLK'] = f"R{row}C{col}_CLK{idx//2}"
            dff.portmap['D'] = f"R{row}C{col}_F{idx}"
            dff.portmap['Q'] = f"R{row}C{col}_Q{idx}"
            dff.portmap['CE'] = f"R{row}C{col}_CE{idx//2}"
            if port:
                dff.portmap[port] = f"R{row}C{col}_LSR{idx//2}"
            mod.wires.update(dff.portmap.values())
            mod.primitives[name] = dff

        elif typ == "IOB":
            try:
                kind, = flags.intersection(iobmap.keys())
            except ValueError:
                continue
            portmap = db.grid[dbrow][dbcol].bels[bel].portmap
            name = f"R{row}C{col}_{kind}_{idx}"
            wires = set(iobmap[kind]['wires'])
            ports = set(chain.from_iterable(iobmap[kind].values())) - wires

            iob = codegen.Primitive(kind, name)

            for port in wires:
                wname = portmap[port]
                iob.portmap[port] = f"R{row}C{col}_{wname}"

            for port in ports:
                iob.portmap[port] = f"R{row}C{col}_{port}{idx}"

            for wires in iobmap[kind]['wires']:
                wnames = [f"R{row}C{col}_{portmap[w]}" for w in wires]
                mod.wires.update(wnames)
            for direction in ['inputs', 'outputs', 'inouts']:
                for wires in iobmap[kind].get(direction, []):
                    wnames = [f"R{row}C{col}_{w}{idx}" for w in wires]
                    getattr(mod, direction).update(wnames)

            mod.primitives[name] = iob

    gnd = codegen.Primitive("GND", "mygnd")
    gnd.portmap["G"] = "VSS"
    mod.primitives["mygnd"] = gnd
    vcc = codegen.Primitive("VCC", "myvcc")
    vcc.portmap["V"] = "VCC"
    mod.primitives["myvcc"] = vcc


if __name__ == "__main__":
    with open(f"{device}.pickle", 'rb') as f:
        db = pickle.load(f)
    bitmap = read_bitstream(sys.argv[1])[0]
    bm = chipdb.tile_bitmap(db, bitmap)
    mod = codegen.Module()
    for idx, t in bm.items():
        row, col = idx
        dbtile = db.grid[row][col]
        print(idx)
        #for bitrow in t:
        #    print(*bitrow, sep='')
        #if idx == (5, 0):
        #    from fuse_h4x import *
        #    fse = readFse(open("/home/pepijn/bin/gowin/IDE/share/device/GW1N-1/GW1N-1.fse", 'rb'))
        #    breakpoint()
        bels, pips = parse_tile_(dbtile, t)
        print(bels)
        #print(pips)
        tile2verilog(row, col, bels, pips, mod, db)
    with open("unpack.v", 'w') as f:
        mod.write(f)

