// Reelfy Studio — native macOS shell.
// Boots the local Python engine (FastAPI) and shows the Studio in a WKWebView.
// The engine lives in the repo (REELFY_HOME or ~/Work/Mixiuh/clipfy/spike); the
// app owns its lifecycle: spawn on launch (unless one is already healthy on the
// port), terminate on quit. Native file pickers, JS dialogs and downloads.
import Cocoa
import WebKit

let PORT = ProcessInfo.processInfo.environment["REELFY_PORT"].flatMap { Int($0) } ?? 8317
let BASE = "http://127.0.0.1:\(PORT)"

let splashHTML = """
<!doctype html><meta charset=utf-8><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0a0c10;font-family:-apple-system,sans-serif">
<div style="text-align:center">
<div style="font-size:34px;font-weight:800;color:#e9edf4">Reel<span style="background:linear-gradient(90deg,#ff5a3c,#ffb020);-webkit-background-clip:text;color:transparent">fy</span>
<span style="font-size:13px;color:#5d6678;letter-spacing:.22em;font-weight:700">STUDIO</span></div>
<div style="margin:26px auto 0;width:26px;height:26px;border:3px solid #252a35;border-top-color:#ff5a3c;border-radius:50%;animation:s .7s linear infinite"></div>
<div style="margin-top:18px;color:#8b95a7;font-size:13px">Iniciando el motor de IA local…</div>
<style>@keyframes s{to{transform:rotate(360deg)}}</style></div>
"""

func errorHTML(_ msg: String) -> String { """
<!doctype html><meta charset=utf-8><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0a0c10;font-family:-apple-system,sans-serif">
<div style="max-width:520px;text-align:center;color:#e9edf4">
<div style="font-size:22px;font-weight:700;margin-bottom:12px">No se pudo iniciar Reelfy</div>
<div style="color:#8b95a7;font-size:14px;line-height:1.6">\(msg)</div>
<div style="color:#5d6678;font-size:12px;margin-top:16px">Registro: ~/Library/Logs/Reelfy.log</div></div>
""" }

class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let rect = NSRect(x: 0, y: 0, width: 1340, height: 940)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Reelfy Studio"
        window.minSize = NSSize(width: 980, height: 680)
        window.center()
        window.setFrameAutosaveName("ReelfyMain")

        let cfg = WKWebViewConfiguration()
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        web = WKWebView(frame: rect, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.autoresizingMask = [.width, .height]
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        web.loadHTMLString(splashHTML, baseURL: nil)
        ensureServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ note: Notification) { server?.terminate() }

    // MARK: engine lifecycle
    func ensureServer() {
        health { alive in
            if alive { self.loadStudio() }
            else {
                self.spawnServer()
                self.waitForServer(deadline: Date().addingTimeInterval(120))
            }
        }
    }
    func health(_ cb: @escaping (Bool) -> Void) {
        var req = URLRequest(url: URL(string: BASE + "/tracks")!)
        req.timeoutInterval = 1.0
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            cb((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }
    func loadStudio() {
        DispatchQueue.main.async { self.web.load(URLRequest(url: URL(string: BASE)!)) }
    }
    func spawnServer() {
        let fm = FileManager.default
        var home: String
        var py: String
        var extraEnv: [String: String] = [:]
        let bundled = (Bundle.main.resourcePath ?? "") + "/engine"
        let bundledPy = bundled + "/python/bin/python3"
        if fm.fileExists(atPath: bundled + "/app/server.py"), fm.isExecutableFile(atPath: bundledPy) {
            // app distribuible: motor embebido, datos del usuario en App Support
            home = bundled
            py = bundledPy
            let data = NSHomeDirectory() + "/Library/Application Support/Reelfy"
            try? fm.createDirectory(atPath: data, withIntermediateDirectories: true)
            extraEnv["REELFY_HOME"] = home
            extraEnv["REELFY_DATA"] = data
        } else {
            // modo desarrollo: el repo en esta máquina
            home = ProcessInfo.processInfo.environment["REELFY_HOME"]
                ?? "\(NSHomeDirectory())/Work/Mixiuh/clipfy/spike"
            py = "\(home)/.venv/bin/python"
        }
        guard fm.isExecutableFile(atPath: py) else {
            showError("No encuentro el motor en <b>\(home)</b>.<br>Define la variable REELFY_HOME o restaura la carpeta del proyecto.")
            return
        }
        let logURL = fm.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Reelfy.log")
        fm.createFile(atPath: logURL.path, contents: nil)
        let log = try? FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: py)
        p.arguments = ["-u", "\(home)/app/server.py"]
        var env = ProcessInfo.processInfo.environment
        env["REELFY_PORT"] = String(PORT)
        for (k, v) in extraEnv { env[k] = v }
        p.environment = env
        p.currentDirectoryURL = URL(fileURLWithPath: home)
        p.standardOutput = log ?? FileHandle.nullDevice
        p.standardError = log ?? FileHandle.nullDevice
        do { try p.run(); server = p }
        catch { showError("El motor no arrancó: \(error.localizedDescription)") }
    }
    func waitForServer(deadline: Date) {
        health { alive in
            if alive { self.loadStudio(); return }
            if let s = self.server, !s.isRunning {
                self.showError("El motor se cerró inesperadamente al arrancar."); return
            }
            if Date() > deadline {
                self.showError("El motor tardó demasiado en arrancar (2 min)."); return
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) {
                self.waitForServer(deadline: deadline)
            }
        }
    }
    func showError(_ msg: String) {
        DispatchQueue.main.async { self.web.loadHTMLString(errorHTML(msg), baseURL: nil) }
    }

    // MARK: menu (needed for ⌘C/⌘V/⌘Q to work in a WKWebView app)
    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Acerca de Reelfy",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Ocultar Reelfy", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Salir de Reelfy", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edición")
        edit.addItem(withTitle: "Deshacer", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Rehacer", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cortar", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copiar", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Pegar", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Seleccionar todo", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "Visualización")
        view.addItem(withTitle: "Recargar", action: #selector(reload(_:)), keyEquivalent: "r")
        viewItem.submenu = view

        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "Ventana")
        win.addItem(withTitle: "Minimizar", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        winItem.submenu = win
        NSApp.mainMenu = main
    }
    @objc func reload(_ sender: Any?) { web.load(URLRequest(url: URL(string: BASE)!)) }

    // MARK: JS dialogs -> native sheets
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = "Reelfy"; a.informativeText = message
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = "Reelfy"; a.informativeText = message
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancelar")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = "Reelfy"; a.informativeText = prompt
        let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        tf.stringValue = defaultText ?? ""
        a.accessoryView = tf
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancelar")
        a.window.initialFirstResponder = tf
        a.beginSheetModal(for: window) { r in
            completionHandler(r == .alertFirstButtonReturn ? tf.stringValue : nil)
        }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let p = NSOpenPanel()
        p.allowsMultipleSelection = parameters.allowsMultipleSelection
        p.canChooseFiles = true; p.canChooseDirectories = false
        p.beginSheetModal(for: window) { r in completionHandler(r == .OK ? p.urls : nil) }
    }

    // MARK: navigation + downloads
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload { decisionHandler(.download); return }
        if let url = navigationAction.request.url,
           navigationAction.navigationType == .linkActivated,
           url.scheme == "http" || url.scheme == "https",
           !url.absoluteString.hasPrefix(BASE) {
            NSWorkspace.shared.open(url); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var dest = dir.appendingPathComponent(suggestedFilename)
        let base = dest.deletingPathExtension().lastPathComponent, ext = dest.pathExtension
        var i = 1
        while FileManager.default.fileExists(atPath: dest.path) {
            dest = dir.appendingPathComponent("\(base)-\(i)\(ext.isEmpty ? "" : "." + ext)"); i += 1
        }
        lastDownload = dest
        completionHandler(dest)
    }
    var lastDownload: URL?
    func downloadDidFinish(_ download: WKDownload) {
        if let f = lastDownload {
            DispatchQueue.main.async { NSWorkspace.shared.activateFileViewerSelecting([f]) }
        }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        DispatchQueue.main.async {
            let a = NSAlert(); a.messageText = "Descarga falló"
            a.informativeText = error.localizedDescription
            a.beginSheetModal(for: self.window) { _ in }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
