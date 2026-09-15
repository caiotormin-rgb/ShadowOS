#!/usr/bin/env python3
"""Render the README chat SVGs to 2x PNGs using librsvg and Cairo."""
from pathlib import Path
import ctypes as C
import ctypes.util
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent

def library(name):
    path = ctypes.util.find_library(name)
    if not path:
        raise RuntimeError(f"Install the {name} shared library to render PNG assets")
    return C.CDLL(path)

class Rect(C.Structure):
    _fields_ = [(name, C.c_double) for name in ('x', 'y', 'width', 'height')]

def bind(lib, name, args, result):
    fn = getattr(lib, name)
    fn.argtypes, fn.restype = args, result
    return fn

def main():
    rsvg, cairo, gobject = library('rsvg-2'), library('cairo'), library('gobject-2.0')
    new = bind(rsvg, 'rsvg_handle_new_from_file', [C.c_char_p, C.c_void_p], C.c_void_p)
    render = bind(rsvg, 'rsvg_handle_render_document', [C.c_void_p, C.c_void_p, C.POINTER(Rect), C.c_void_p], C.c_int)
    surface_new = bind(cairo, 'cairo_image_surface_create', [C.c_int, C.c_int, C.c_int], C.c_void_p)
    create = bind(cairo, 'cairo_create', [C.c_void_p], C.c_void_p)
    write = bind(cairo, 'cairo_surface_write_to_png', [C.c_void_p, C.c_char_p], C.c_int)
    destroy = bind(cairo, 'cairo_destroy', [C.c_void_p], None)
    surface_destroy = bind(cairo, 'cairo_surface_destroy', [C.c_void_p], None)
    unref = bind(gobject, 'g_object_unref', [C.c_void_p], None)
    for path in sorted(ROOT.glob('chat-*.svg')):
        element = ET.parse(path).getroot()
        width, height = (int(element.attrib[key]) * 2 for key in ('width', 'height'))
        handle = new(str(path).encode(), None)
        if not handle:
            raise RuntimeError(f'Cannot load {path.name}')
        surface = surface_new(0, width, height)
        context = create(surface)
        destination = path.with_suffix('.png')
        try:
            if not render(handle, context, C.byref(Rect(0, 0, width, height)), None):
                raise RuntimeError(f'Cannot render {path.name}')
            if write(surface, str(destination).encode()) != 0:
                raise RuntimeError(f'Cannot write {destination.name}')
        finally:
            destroy(context)
            surface_destroy(surface)
            unref(handle)
        print(f'{destination.name}: {width} × {height}')

if __name__ == '__main__':
    main()
