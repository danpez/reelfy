"""Config de dmgbuild para el DMG de marca de Reelfy.
Escribe el layout (fondo, posiciones, tamaño de ventana) en el .DS_Store
directamente — sin Finder/AppleScript, así funciona desde consola.
Uso: dmgbuild -s dmg_settings.py -D app=<ruta> Reelfy <salida.dmg>
"""
import os

app = defines.get("app", "dist/Reelfy.app")  # noqa: F821 (dmgbuild inyecta `defines`)
appname = os.path.basename(app)

format = "UDZO"
compression_level = 9
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extension = [appname]

background = "build/dmg-bg.tiff"
window_rect = ((320, 160), (660, 460))
default_view = "icon-view"
show_icon_preview = False
include_icon_view_settings = True
include_list_view_settings = False
arrange_by = None
icon_size = 112
text_size = 12
label_pos = "bottom"

icon_locations = {
    appname: (172, 232),
    "Applications": (488, 232),
}
