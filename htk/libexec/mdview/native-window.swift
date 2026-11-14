// Optional macOS presentation adapter for the existing live Markdown renderer.
import AppKit
import WebKit

final class PreviewWindow: NSObject, NSApplicationDelegate, NSWindowDelegate,
                           WKNavigationDelegate, WKScriptMessageHandler {
    let address: URL
    let title: String
    let focus: Bool
    var window: NSWindow!
    var webView: WKWebView!

    init(address: URL, title: String, focus: Bool) {
        self.address = address
        self.title = title
        self.focus = focus
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "htkPreview")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = false

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 920, height: 760),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "\(title) — HTK"
        window.minSize = NSSize(width: 360, height: 260)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = webView
        window.center()
        buildMenus()
        webView.load(URLRequest(url: address))
        // Display the document without activating this application or taking keys
        // away from whichever application the user is currently working in.
        window.orderFrontRegardless()
        if focus { reveal() }
    }

    func buildMenus() {
        let menu = NSMenu()
        let appItem = NSMenuItem(title: "HTK Preview", action: nil, keyEquivalent: "")
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit HTK Preview", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let fileItem = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        let fileMenu = NSMenu(title: "File")
        fileMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        let reload = fileMenu.addItem(withTitle: "Reload", action: #selector(reloadDocument(_:)), keyEquivalent: "r")
        reload.target = self
        fileItem.submenu = fileMenu
        menu.addItem(fileItem)
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Copy", action: Selector(("copy:")), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Select All", action: Selector(("selectAll:")), keyEquivalent: "a")
        editItem.submenu = editMenu
        menu.addItem(editItem)
        NSApp.mainMenu = menu
    }

    @objc func reloadDocument(_ sender: Any?) { webView.reload() }

    func reveal() {
        window.deminiaturize(nil)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func isDocument(_ url: URL) -> Bool {
        url.scheme == address.scheme && url.host == address.host &&
        url.port == address.port && url.path == address.path &&
        url.user == nil && url.password == nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if isDocument(url) && navigationAction.targetFrame?.isMainFrame != false {
            decisionHandler(.allow)
            return
        }
        // A document link opens in the user's normal application. Remote pages
        // never replace the local document or gain access to its native bridge.
        if navigationAction.navigationType == .linkActivated &&
            ["http", "https", "mailto"].contains(url.scheme?.lowercased() ?? "") {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              let url = message.frameInfo.request.url, isDocument(url),
              message.body as? String == "focus" else { return }
        // Only an explicit --focus request produces the renderer's focus event.
        reveal()
    }

    func windowWillClose(_ notification: Notification) { NSApp.terminate(nil) }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let arguments = CommandLine.arguments
guard arguments.count >= 3, let address = URL(string: arguments[1]),
      address.scheme == "http", address.host == "127.0.0.1", address.port != nil,
      address.user == nil, address.password == nil else {
    fputs("HTK preview requires a local document URL and title.\n", stderr)
    exit(2)
}
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let preview = PreviewWindow(address: address, title: arguments[2],
                            focus: arguments.dropFirst(3).contains("--focus"))
application.delegate = preview
application.run()
