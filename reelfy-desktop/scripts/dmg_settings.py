"""Layout de marca del DMG de Reelfy para dmgbuild (SIN Finder/AppleScript:
escribe el .DS_Store directamente, por eso el resultado es idéntico en local y
en CI headless — el AppleScript de Tauri falla silencioso en runners sin sesión
gráfica y el DMG sale plano).

Uso (desde reelfy-desktop/src-tauri):
  dmgbuild -s ../scripts/dmg_settings.py -D app=<ruta .app> Reelfy <salida.dmg>
"""
import os

app = defines.get("app", "target/release/bundle/macos/Reelfy.app")  # noqa: F821
appname = os.path.basename(app)

format = "UDZO"
compression_level = 9
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extension = [appname]

# mismo diseño trabajado: fondo navy + glow coral + flecha (1x/@2x en el tiff)
background = "dmg-background.tiff"
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
