import AppKit
import WebKit
import LabyrinthKit

/// Owns the window, the web view and its delegates. Shows the page the app-owned server serves.
final class MainWindowController: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private static let pageBackground = NSColor(srgbRed: 0x0b / 255.0, green: 0x0d / 255.0, blue: 0x12 / 255.0, alpha: 1)
    private static let hardening = """
    document.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
    (function () { var s = document.createElement('style'); s.textContent = 'html{overscroll-behavior:none}'; (document.head || document.documentElement).appendChild(s); })();
    """

    private let window: NSWindow
    private let webView: WKWebView
    private var loaded: ServerHandshake?

    var isVisible: Bool { window.isVisible }

    override init() {
        let config = WKWebViewConfiguration()
        let userContent = WKUserContentController()
        userContent.addUserScript(WKUserScript(source: Self.hardening, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController = userContent
        webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsLinkPreview = false
        webView.allowsMagnification = false
        webView.underPageBackgroundColor = Self.pageBackground
        webView.autoresizingMask = [.width, .height]

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Labyrinth Cleaner"
        window.minSize = NSSize(width: 900, height: 600)
        window.isReleasedWhenClosed = false
        window.backgroundColor = Self.pageBackground
        webView.frame = window.contentView?.bounds ?? .zero
        window.contentView?.addSubview(webView)
        if !window.setFrameUsingName("MainWindow") { window.center() }
        window.setFrameAutosaveName("MainWindow")

        super.init()
        window.delegate = self
        webView.navigationDelegate = self
        webView.uiDelegate = self
        userContent.add(self, name: "labyrinth")
    }

    /// Loads the server's page (once per server) and brings the window forward, with a Dock icon.
    func show(handshake: ServerHandshake) {
        if loaded?.url != handshake.url {
            loaded = handshake
            webView.load(URLRequest(url: handshake.url))
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    /// The server is gone: the next show(handshake:) must load the new server's URL.
    func forgetLoadedServer() { loaded = nil }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }

    // MARK: navigation lock

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let port = loaded?.port else { decisionHandler(.cancel); return }
        switch navigationDecision(url: url, serverPort: port) {
        case .allow: decisionHandler(.allow)
        case .openExternally: NSWorkspace.shared.open(url); decisionHandler(.cancel)
        case .cancel: decisionHandler(.cancel)
        }
    }

    /// No popups.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, let port = loaded?.port,
           navigationDecision(url: url, serverPort: port) == .openExternally {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    // MARK: bridge

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let port = loaded?.port else { return }
        let origin = message.frameInfo.securityOrigin
        guard parseBridgeMessage(body: message.body, originHost: origin.host, originPort: origin.port, serverPort: port) == .pickProjectFolder
        else { return }
        pickFolder()
    }

    private func pickFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Add Folder"
        panel.directoryURL = URL(fileURLWithPath: NSHomeDirectory())
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            self?.webView.evaluateJavaScript(addedFolderScript(path: url.path), completionHandler: nil)
        }
    }
}
