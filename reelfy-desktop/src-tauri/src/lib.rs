// Reelfy Studio — cáscara multiplataforma (Tauri 2).
// Arranca el motor Python local (FastAPI), muestra un splash y navega el
// webview al Studio cuando el motor responde. Al cerrar, mata el motor.
// El motor vive embebido en la app (resource_dir/engine) o, en dev, en el repo
// (REELFY_HOME o ~/Work/Mixiuh/clipfy/spike).
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::webview::DownloadEvent;
use tauri::{Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

const PORT: u16 = 8317;

/// Proceso del motor, para matarlo al salir.
struct Engine(Mutex<Option<Child>>);

fn base_url() -> String {
    format!("http://127.0.0.1:{PORT}")
}

/// El motor responde el endpoint /tracks con 200.
fn healthy() -> bool {
    ureq::get(&format!("{}/tracks", base_url()))
        .timeout(Duration::from_millis(1200))
        .call()
        .map(|r| r.status() == 200)
        .unwrap_or(false)
}

/// Resuelve (python, server.py, home_embebido). home_embebido = Some solo en la
/// app distribuible (motor bajo resource_dir/engine); None en modo dev (repo).
fn resolve_engine(app: &tauri::AppHandle) -> Option<(PathBuf, PathBuf, Option<PathBuf>)> {
    let win = cfg!(target_os = "windows");

    // 1) app distribuible: motor embebido en resource_dir/engine
    if let Ok(res) = app.path().resource_dir() {
        let eng = res.join("engine");
        let server = eng.join("app").join("server.py");
        let py = if win {
            eng.join("python").join("python.exe")
        } else {
            eng.join("python").join("bin").join("python3")
        };
        if server.exists() && py.exists() {
            return Some((py, server, Some(eng)));
        }
    }

    // 2) dev: REELFY_HOME o ~/Work/Mixiuh/clipfy/spike
    let home: PathBuf = std::env::var("REELFY_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            app.path()
                .home_dir()
                .map(|h| h.join("Work/Mixiuh/clipfy/spike"))
                .unwrap_or_else(|_| PathBuf::from("spike"))
        });
    let server = home.join("app").join("server.py");
    let py = if win {
        home.join(".venv").join("Scripts").join("python.exe")
    } else {
        home.join(".venv").join("bin").join("python")
    };
    if server.exists() && py.exists() {
        return Some((py, server, None));
    }
    None
}

/// Lanza el motor Python. Devuelve el Child o None si no encontró el motor.
fn spawn_engine(app: &tauri::AppHandle) -> Option<Child> {
    let (py, server, bundled_home) = match resolve_engine(app) {
        Some(t) => t,
        None => {
            eprintln!("[reelfy] no encontré el motor (ni embebido ni en el repo)");
            return None;
        }
    };
    eprintln!("[reelfy] motor: {} {}", py.display(), server.display());

    let mut cmd = Command::new(&py);
    cmd.arg("-u").arg(&server);
    cmd.env("REELFY_PORT", PORT.to_string());
    if let Some(home) = &bundled_home {
        // app distribuible: datos del usuario en app_data_dir (escribible)
        cmd.env("REELFY_HOME", home);
        if let Ok(data) = app.path().app_data_dir() {
            let _ = std::fs::create_dir_all(&data);
            cmd.env("REELFY_DATA", data);
        }
        cmd.current_dir(home);
    }
    match cmd.spawn() {
        Ok(child) => Some(child),
        Err(e) => {
            eprintln!("[reelfy] el motor no arrancó: {e}");
            None
        }
    }
}

fn show_error(win: &WebviewWindow, msg: &str) {
    let inner = serde_json::to_string(&format!(
        "<div class='err'><h1>No se pudo iniciar Reelfy</h1><p>{msg}</p></div>"
    ))
    .unwrap_or_else(|_| "''".into());
    let _ = win.eval(&format!(
        "var w=document.getElementById('wrap'); if(w) w.innerHTML={inner};"
    ));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Engine(Mutex::new(None)))
        .setup(|app| {
            // Menú nativo (en macOS es necesario para ⌘C/⌘V/⌘Q dentro del webview).
            #[cfg(target_os = "macos")]
            {
                use tauri::menu::{Menu, PredefinedMenuItem, Submenu};
                let app_menu = Submenu::with_items(
                    app,
                    "Reelfy",
                    true,
                    &[
                        &PredefinedMenuItem::about(app, Some("Reelfy"), None)?,
                        &PredefinedMenuItem::separator(app)?,
                        &PredefinedMenuItem::hide(app, None)?,
                        &PredefinedMenuItem::quit(app, None)?,
                    ],
                )?;
                let edit_menu = Submenu::with_items(
                    app,
                    "Edición",
                    true,
                    &[
                        &PredefinedMenuItem::undo(app, None)?,
                        &PredefinedMenuItem::redo(app, None)?,
                        &PredefinedMenuItem::separator(app)?,
                        &PredefinedMenuItem::cut(app, None)?,
                        &PredefinedMenuItem::copy(app, None)?,
                        &PredefinedMenuItem::paste(app, None)?,
                        &PredefinedMenuItem::select_all(app, None)?,
                    ],
                )?;
                let menu = Menu::with_items(app, &[&app_menu, &edit_menu])?;
                app.set_menu(menu)?;
            }

            // Ventana creada desde Rust para poder enganchar el manejo de descargas:
            // el Studio descarga el video con <a download> -> lo guardamos en Downloads.
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Reelfy Studio")
                .inner_size(1340.0, 940.0)
                .min_inner_size(980.0, 680.0)
                .center()
                .on_download(|webview, event| {
                    if let DownloadEvent::Requested { destination, .. } = event {
                        if let Ok(dir) = webview.path().download_dir() {
                            if let Some(name) = destination.file_name().map(|n| n.to_owned()) {
                                let mut dest = dir.join(&name);
                                let stem = std::path::Path::new(&name)
                                    .file_stem()
                                    .map(|s| s.to_string_lossy().into_owned())
                                    .unwrap_or_else(|| "reelfy".into());
                                let ext = std::path::Path::new(&name)
                                    .extension()
                                    .map(|e| format!(".{}", e.to_string_lossy()))
                                    .unwrap_or_default();
                                let mut i = 1;
                                while dest.exists() {
                                    dest = dir.join(format!("{stem}-{i}{ext}"));
                                    i += 1;
                                }
                                *destination = dest;
                            }
                        }
                    }
                    true
                })
                .build()?;

            let handle = app.handle().clone();

            // 1) ¿ya hay un motor sano (dev con el server ya corriendo)? si no, lo lanzamos.
            if !healthy() {
                let child = spawn_engine(&handle);
                let state: tauri::State<Engine> = handle.state();
                *state.0.lock().unwrap() = child;
            }

            // 2) hilo que espera a que el motor responda y navega el webview.
            std::thread::spawn(move || {
                let win = match handle.get_webview_window("main") {
                    Some(w) => w,
                    None => return,
                };
                let deadline = Instant::now() + Duration::from_secs(180);
                loop {
                    if healthy() {
                        if let Ok(url) = tauri::Url::parse(&base_url()) {
                            let _ = win.navigate(url);
                        }
                        return;
                    }
                    // ¿el motor murió al arrancar?
                    {
                        let state: tauri::State<Engine> = handle.state();
                        let mut guard = state.0.lock().unwrap();
                        if let Some(child) = guard.as_mut() {
                            if let Ok(Some(_)) = child.try_wait() {
                                show_error(&win, "El motor se cerró inesperadamente al arrancar. Revisa el registro.");
                                return;
                            }
                        }
                    }
                    if Instant::now() > deadline {
                        show_error(&win, "El motor tardó demasiado en arrancar (2 min).");
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state: tauri::State<Engine> = window.state();
                let child = state.0.lock().unwrap().take();
                if let Some(mut c) = child {
                    // SIGTERM primero: así uvicorn sale limpio y su atexit
                    // TERMINA el ollama embebido. Con kill() directo (SIGKILL)
                    // el ollama quedaba huérfano comiendo RAM tras cerrar la app.
                    #[cfg(unix)]
                    {
                        unsafe { libc::kill(c.id() as i32, libc::SIGTERM) };
                        for _ in 0..30 {
                            if matches!(c.try_wait(), Ok(Some(_))) {
                                return;
                            }
                            std::thread::sleep(Duration::from_millis(100));
                        }
                    }
                    let _ = c.kill(); // no salió a la buena (o Windows): forzar
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
