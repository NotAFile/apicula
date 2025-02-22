import sys
import os
import re
import pickle
import numpy as np
import json
import chipdb
import bslib
from wirenames import wirenames, wirenumbers

device = os.getenv("DEVICE")
if not device:
    raise Exception("DEVICE not set")

def get_bels(data):
    belre = re.compile(r"R(\d+)C(\d+)_(?:SLICE|IOB)(\w)")
    for cell in data['modules']['top']['cells'].values():
        bel = cell['attributes']['NEXTPNR_BEL']
        row, col, num = belre.match(bel).groups() 
        yield (cell['type'], int(row), int(col), num, cell['parameters'])

def get_pips(data):
    pipre = re.compile(r"R(\d+)C(\d+)_(\w+)_(\w+);")
    for net in data['modules']['top']['netnames'].values():
        routing = net['attributes']['ROUTING']
        pips = pipre.findall(routing)
        for row, col, src, dest in pips:
            yield int(row), int(col), src, dest

def infovaluemap(infovalue, start=2):
    return {tuple(iv[:start]):iv[start:] for iv in infovalue}

def place(db, tilemap, bels):
    for typ, row, col, num, attr in bels:
        tiledata = db.grid[row-1][col-1]
        tile = tilemap[(row-1, col-1)]
        if typ == "GENERIC_SLICE":
            lutmap = tiledata.bels[f'LUT{num}'].flags
            init = str(attr['INIT'])
            init = init*(16//len(init))
            for bitnum, lutbit in enumerate(init[::-1]):
                if lutbit == '0':
                    fuses = lutmap[bitnum]
                    for row, col in fuses:
                        tile[row][col] = 1

            #if attr["FF_USED"]: # Maybe it *always* needs the DFF
            if True:
                dffbits = tiledata.bels[f'DFF{num}'].modes['DFF']
                for row, col in dffbits:
                    tile[row][col] = 1

        elif typ == "GENERIC_IOB":
            assert sum([int(v, 2) for v in attr.values()]) <= 1, "Complex IOB unsuported"
            iob = tiledata.bels[f'IOB{num}']
            if int(attr["INPUT_USED"], 2):
                bits = iob.modes['IBUF'] | iob.flags.get('IBUFC', set())
            elif int(attr["OUTPUT_USED"], 2):
                bits = iob.modes['OBUF'] | iob.flags.get('OBUFC', set())
            else:
                raise ValueError("IOB has no in or output")
            for r, c in bits:
                tile[r][c] = 1

            #bank enable
            if row == 1: # top bank
                bank = 0
                brow = 0
                bcol = 0
            elif row == db.rows: # bottom bank
                bank = 2
                brow = db.rows-1
                bcol = db.cols-1
            elif col == 1: # left bank
                bank = 3
                brow = db.rows-1
                bcol = 0
            elif col == db.cols: # right bank
                bank = 1
                brow = 0
                bcol = db.cols-1
            tiledata = db.grid[brow][bcol]
            tile = tilemap[(brow, bcol)]
            bits = tiledata.bels['BANK'].modes['DEFAULT']
            for row, col in bits:
                tile[row][col] = 1


def route(db, tilemap, pips):
    for row, col, src, dest in pips:
        tiledata = db.grid[row-1][col-1]
        tile = tilemap[(row-1, col-1)]

        try:
            bits = tiledata.pips[dest][src]
        except KeyError:
            print(src, dest, "not found in tile", row, col)
            breakpoint()
            continue
        for row, col in bits:
            tile[row][col] = 1

def header_footer(db, bs):
    """
    Generate fs header and footer
    Currently limited to checksum with
    CRC_check and security_bit_enable set
    """
    bs = np.fliplr(bs)
    bs=np.packbits(bs, axis=1)
    # configuration data checksum is computed on all
    # data in 16bit format
    bb = np.array(bs.flat)

    res = int(bb[0::2].sum() * pow(2,8) + bb[1::2].sum())
    checksum = res & 0xffff
    db.cmd_hdr[0] = bytearray.fromhex(f"{checksum:x}")

    # same task for line 2 in footer
    db.cmd_ftr[1] = bytearray.fromhex(f"{0x0A << 56 | checksum:016x}")

if __name__ == '__main__':
    with open(f"{device}.pickle", 'rb') as f:
        db = pickle.load(f)
    with open(sys.argv[1]) as f:
        pnr = json.load(f)
    tilemap = chipdb.tile_bitmap(db, db.template, empty=True)
    bels = get_bels(pnr)
    place(db, tilemap, bels)
    pips = get_pips(pnr)
    route(db, tilemap, pips)
    res = chipdb.fuse_bitmap(db, tilemap)
    header_footer(db, res)
    bslib.display('pack.png', res)
    bslib.write_bitstream('pack.fs', res, db.cmd_hdr, db.cmd_ftr)
