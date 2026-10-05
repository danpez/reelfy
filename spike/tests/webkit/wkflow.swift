// Corre un flujo de prueba (JavaScript) dentro de un WKWebView, el motor de la app de escritorio.
// uso: swiftc -O tests/webkit/wkflow.swift -o <bin> && <bin> <url> <flujo.js> <carpeta de capturas> [ancho] [alto] [segundos]
//
// Como la app (Tauri 2 + wry en macOS), el WKWebView NO responde los dialogos nativos de JavaScript:
// wry solo implementa el selector de archivos, el permiso de camara/microfono y nuevas ventanas, asi que
// confirm() devuelve false al instante, alert() no muestra nada y prompt() devuelve null. Esta prueba
// usa el mismo comportamiento (su delegado de interfaz tampoco los implementa) y ademas los CUENTA
// (window.__nativeDialogs) para que el flujo falle si la app los vuelve a usar.
//
// El flujo es el CUERPO de una funcion async (callAsyncJavaScript) que devuelve un texto JSON con
// {ok, steps, ...}. Dentro puede pedir capturas: `await window.__snap("nombre")` guarda
// <carpeta>/<nombre>.png (pantalla tal cual se ve, a 1x). Imprime el JSON del flujo en stdout.
import Cocoa
import WebKit

let args = CommandLine.arguments
guard args.count >= 4 else {
    FileHandle.standardError.write("uso: wkflow <url> <flujo.js> <capturas> [ancho] [alto] [segundos]\n".data(using: .utf8)!)
    exit(2)
}
let URLS = args[1]
let FLOW = (try? String(contentsOfFile: args[2], encoding: .utf8)) ?? ""
let OUT = args[3]
let W = args.count > 4 ? Double(args[4]) ?? 1440 : 1440
let H = args.count > 5 ? Double(args[5]) ?? 900 : 900
let LIMIT = args.count > 6 ? Double(args[6]) ?? 300 : 300
try? FileManager.default.createDirectory(atPath: OUT, withIntermediateDirectories: true)

// Cuenta los dialogos nativos ANTES de que corra la pagina (sin cambiar lo que devuelven en WebKit:
// la llamada original sigue ocurriendo y, sin delegado, devuelve false / null).
let COUNT = """
(function(){
  window.__nativeDialogs = [];
  for (const k of ["confirm", "alert", "prompt"]) {
    const orig = window[k];
    window[k] = function (m) { window.__nativeDialogs.push(k + ": " + String(m == null ? "" : m).slice(0, 120)); return orig.apply(window, arguments); };
  }
})();
"""

// La ventana de prueba casi no se ve: WebKit no avanza las animaciones CSS ni los cuadros de
// requestAnimationFrame. Antes de cada captura se terminan las animaciones (la entrada de un dialogo)
// y se dibujan los ejemplos de Estilo una vez (Estilo.Ticker.flush).
let SNAP_JS = """
window.__snap = async function (n) {
  try { document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} }); } catch (e) {}
  try { if (window.Estilo && Estilo.Ticker && Estilo.Ticker.flush) Estilo.Ticker.flush(); } catch (e) {}
  await new Promise(r => setTimeout(r, 80));
  return window.webkit.messageHandlers.snap.postMessage(String(n));
};
"""

class Snap: NSObject, WKScriptMessageHandlerWithReply {
    weak var web: WKWebView?
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        let name = (m.body as? String) ?? "captura"
        guard let web = web else { replyHandler(false, nil); return }
        let cfg = WKSnapshotConfiguration()
        cfg.snapshotWidth = NSNumber(value: W)
        web.takeSnapshot(with: cfg) { img, _ in
            var ok = false
            if let img = img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
               let png = rep.representation(using: .png, properties: [:]) {
                ok = (try? png.write(to: URL(fileURLWithPath: "\(OUT)/\(name).png"))) != nil
            }
            replyHandler(ok, nil)
        }
    }
}

class App: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var win: NSWindow!
    var web: WKWebView!
    let snap = Snap()
    var started = false
    func applicationDidFinishLaunching(_ n: Notification) {
        let cfg = WKWebViewConfiguration()
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.userContentController.addUserScript(WKUserScript(source: COUNT, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        cfg.userContentController.addScriptMessageHandler(snap, contentWorld: .page, name: "snap")
        cfg.userContentController.addUserScript(WKUserScript(source: SNAP_JS, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: W, height: H), configuration: cfg)
        snap.web = web
        web.navigationDelegate = self
        // sin uiDelegate: igual que wry, los paneles de JavaScript no se muestran
        win = NSWindow(contentRect: NSRect(x: 40, y: 40, width: W, height: H), styleMask: [.titled], backing: .buffered, defer: false)
        win.title = "Reelfy WebKit flow"
        win.contentView = web
        win.alphaValue = 0.02          // casi invisible, pero en pantalla: WebKit no pausa el dibujo
        win.ignoresMouseEvents = true
        win.orderFrontRegardless()
        web.load(URLRequest(url: URL(string: URLS)!))
        DispatchQueue.main.asyncAfter(deadline: .now() + LIMIT) {
            print("{\"ok\":false,\"error\":\"tiempo agotado\"}")
            NSApp.terminate(nil)
        }
    }
    func webView(_ w: WKWebView, didFinish nav: WKNavigation!) {
        if started { return }
        started = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.run() }
    }
    func run() {
        web.callAsyncJavaScript(FLOW, arguments: [:], in: nil, in: .page) { res in
            switch res {
            case .success(let v): print(v as? String ?? "\(v)")
            case .failure(let e):
                let msg = e.localizedDescription.replacingOccurrences(of: "\"", with: "'")
                print("{\"ok\":false,\"error\":\"\(msg)\"}")
            }
            NSApp.terminate(nil)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let d = App()
app.delegate = d
app.run()
