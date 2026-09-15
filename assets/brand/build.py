#!/usr/bin/env python3
"""Build portable SVGs with real Geist outlines. See docs/VISUAL-IDENTITY.md."""
from pathlib import Path
import ctypes as C
import ctypes.util
import json
import xml.etree.ElementTree as ET
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

ROOT = Path(__file__).resolve().parent
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
HB = C.CDLL(ctypes.util.find_library('harfbuzz') or 'libharfbuzz.so.0')

class Info(C.Structure):
    _fields_ = [(k, C.c_uint32) for k in ('codepoint', 'mask', 'cluster', 'var1', 'var2')]

class Position(C.Structure):
    _fields_ = [(k, C.c_int32) for k in ('x_advance', 'y_advance', 'x_offset', 'y_offset')] + [('var', C.c_uint32)]

def bind(name, args, result):
    fn = getattr(HB, name)
    fn.argtypes, fn.restype = args, result
    return fn

blob_create = bind('hb_blob_create', [C.c_char_p, C.c_uint, C.c_int, C.c_void_p, C.c_void_p], C.c_void_p)
face_create = bind('hb_face_create', [C.c_void_p, C.c_uint], C.c_void_p)
font_create = bind('hb_font_create', [C.c_void_p], C.c_void_p)
font_funcs = bind('hb_ot_font_set_funcs', [C.c_void_p], None)
font_scale = bind('hb_font_set_scale', [C.c_void_p, C.c_int, C.c_int], None)
buffer_create = bind('hb_buffer_create', [], C.c_void_p)
buffer_add = bind('hb_buffer_add_utf8', [C.c_void_p, C.c_char_p, C.c_int, C.c_uint, C.c_int], None)
buffer_guess = bind('hb_buffer_guess_segment_properties', [C.c_void_p], None)
shape = bind('hb_shape', [C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint], None)
infos = bind('hb_buffer_get_glyph_infos', [C.c_void_p, C.POINTER(C.c_uint)], C.POINTER(Info))
positions = bind('hb_buffer_get_glyph_positions', [C.c_void_p, C.POINTER(C.c_uint)], C.POINTER(Position))
for resource in ('blob', 'face', 'font', 'buffer'):
    bind('hb_' + resource + '_destroy', [C.c_void_p], None)

class Typeface:
    def __init__(self, path):
        self.tt = TTFont(path)
        self.glyphs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()
        self.units = self.tt['head'].unitsPerEm
        data = path.read_bytes()
        self.blob = blob_create(data, len(data), 0, None, None)
        self.face = face_create(self.blob, 0)
        self.font = font_create(self.face)
        font_funcs(self.font)
        font_scale(self.font, self.units, self.units)

    def outline(self, text, x, y, size, spacing, anchor):
        buf = buffer_create()
        try:
            data = text.encode('utf-8')
            buffer_add(buf, data, len(data), 0, -1)
            buffer_guess(buf)
            shape(self.font, buf, None, 0)
            count = C.c_uint()
            glyph_info, glyph_pos = infos(buf, C.byref(count)), positions(buf, C.byref(count))
            scale = size / self.units
            width = sum(glyph_pos[i].x_advance * scale for i in range(count.value)) + max(0, count.value - 1) * spacing
            x -= width / 2 if anchor == 'middle' else width if anchor == 'end' else 0
            pen = SVGPathPen(self.glyphs, ntos=lambda n: format(n, '.3f').rstrip('0').rstrip('.') if n else '0')
            for i in range(count.value):
                info, pos = glyph_info[i], glyph_pos[i]
                if info.codepoint == 0:
                    raise ValueError('Missing glyph in ' + repr(text))
                transform = (scale, 0, 0, -scale, x + pos.x_offset * scale, y - pos.y_offset * scale)
                self.glyphs[self.order[info.codepoint]].draw(TransformPen(pen, transform))
                x += pos.x_advance * scale + spacing
                y -= pos.y_advance * scale
            return pen.getCommands(), width
        finally:
            HB.hb_buffer_destroy(buf)

    def close(self):
        self.tt.close()
        HB.hb_font_destroy(self.font)
        HB.hb_face_destroy(self.face)
        HB.hb_blob_destroy(self.blob)


def main():
    fonts = {400: Typeface(ROOT / 'fonts/Geist-Regular.ttf'), 500: Typeface(ROOT / 'fonts/Geist-Medium.ttf')}
    summary = []
    try:
        for source in sorted((ROOT / 'source').glob('*.svg')):
            tree = ET.parse(source)
            lines = []
            def convert(parent, inherited):
                props = inherited | parent.attrib
                for element in list(parent):
                    values = props | element.attrib
                    if element.tag == '{' + NS + '}text':
                        text = ''.join(element.itertext())
                        weight = 500 if int(values.get('font-weight', '400')) >= 500 else 400
                        x, y = float(values.get('x', 0)), float(values.get('y', 0))
                        size, spacing = float(values.get('font-size', 16)), float(values.get('letter-spacing', 0))
                        anchor = values.get('text-anchor', 'start')
                        data, width = fonts[weight].outline(text, x, y, size, spacing, anchor)
                        outline = ET.Element('{' + NS + '}path', {'d': data, 'fill': values.get('fill', '#E7ECEF'), 'aria-label': text})
                        if 'transform' in element.attrib:
                            outline.set('transform', element.attrib['transform'])
                        at = list(parent).index(element)
                        parent.remove(element)
                        parent.insert(at, outline)
                        lines.append({'text': text, 'x': x, 'y': y, 'width': round(width, 2), 'anchor': anchor})
                    else:
                        convert(element, props)
                for key in list(parent.attrib):
                    if key.startswith('font-') or key in ('letter-spacing', 'text-anchor'):
                        del parent.attrib[key]
            convert(tree.getroot(), {})
            ET.indent(tree, space='  ')
            destination = ROOT / source.name
            tree.write(destination, encoding='unicode', xml_declaration=False)
            with destination.open('a') as f:
                f.write('\n')
            summary.append({'asset': source.name, 'text_outlines': len(lines), 'lines': lines})
    finally:
        for font in fonts.values():
            font.close()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
